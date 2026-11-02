"""Free headline sources (RSS): Yahoo Finance per-ticker feed, Google News as fallback. Stdlib only."""
import email.utils, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0"}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        return ET.fromstring(r.read())


def _items(root, default_src):
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate")
        if not title or not pub:
            continue
        try:
            ts = email.utils.parsedate_to_datetime(pub).timestamp()
        except Exception:
            continue
        src = (it.findtext("source") or default_src).strip()
        out.append({"title": title, "src": src, "ts": ts})
    return out


def _for_symbol(sym, name):
    try:
        got = _items(_get(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(sym)}&region=US&lang=en-US"), "Yahoo Finance")
    except Exception:
        got = []
    if not got:
        try:
            q = urllib.parse.quote(f'"{name}" stock')
            got = _items(_get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"), "Google News")
        except Exception:
            got = []
    return [{**g, "sym": sym} for g in got]


def fetch_all(symbols, names):
    """Returns raw headlines [{sym,title,src,ts}], newest first, de-duplicated. Never raises."""
    try:
        with ThreadPoolExecutor(6) as ex:
            res = list(ex.map(lambda s: _for_symbol(s, names.get(s, s)), symbols))
    except Exception:
        return []
    seen, out = set(), []
    for item in sorted((i for r in res for i in r), key=lambda i: -i["ts"]):
        k = item["title"].lower()
        if k not in seen:
            seen.add(k); out.append(item)
    return out
