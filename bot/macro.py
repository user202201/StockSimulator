"""Big-picture inputs for the AI traders: market-wide gauges (volatility, rates, dollar, oil, gold, sectors) from Yahoo's
chart endpoint and macro headlines (politics, Fed, trade, geopolitics, AI regulation) from Google News RSS. Stdlib only,
never raises: if a source fails the AI simply gets less context."""
import json, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

import news as newsmod

GAUGES = [
    ("^VIX", "VIX (fear index)", "vol"),
    ("^TNX", "US 10-year yield %", "rate"),
    ("DX-Y.NYB", "US dollar index", "fx"),
    ("CL=F", "WTI oil", "cmd"),
    ("GC=F", "Gold", "cmd"),
    ("SOXX", "Semiconductors ETF", "sector"),
    ("XLK", "Technology sector", "sector"),
    ("XLF", "Financials sector", "sector"),
    ("XLE", "Energy sector", "sector"),
    ("XLV", "Health care sector", "sector"),
    ("XLU", "Utilities sector (defensive)", "sector"),
    ("XLP", "Consumer staples (defensive)", "sector"),
]

TOPICS = [
    ("markets", "Wall Street stock market today"),
    ("fed", "Federal Reserve interest rates inflation"),
    ("politics", "Congress election White House economy policy"),
    ("ai_policy", "AI regulation data centers state legislation"),
    ("trade", "tariffs trade China export controls chips"),
    ("world", "geopolitics war Middle East Taiwan Russia markets"),
    ("energy", "oil prices OPEC"),
]


def _gauge(item):
    sym, name, kind = item
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=3mo&interval=1d"
        req = urllib.request.Request(url, headers=newsmod.UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.load(r)["chart"]["result"][0]
        c = [x for x in res["indicators"]["quote"][0]["close"] if x is not None]
        live = res["meta"].get("regularMarketPrice")
        if live:
            c[-1] = live
        if len(c) < 22:
            return None
        return {"sym": sym, "name": name, "kind": kind, "price": round(c[-1], 2),
                "d1": round(c[-1] / c[-2] - 1, 4), "d5": round(c[-1] / c[-6] - 1, 4), "d20": round(c[-1] / c[-21] - 1, 4)}
    except Exception:
        return None


def _topic(item):
    key, query = item
    try:
        q = urllib.parse.quote(query + " when:2d")
        got = newsmod._items(newsmod._get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"), "Google News")
    except Exception:
        got = []
    return [{**g, "topic": key} for g in sorted(got, key=lambda g: -g["ts"])[:4]]


def fetch(now_ts, per_topic=3, max_age_h=48):
    """Returns {"gauges": [...], "headlines": [{id, sym, topic, title, src, age_h}]}. Never raises."""
    try:
        with ThreadPoolExecutor(6) as ex:
            gauges = [g for g in ex.map(_gauge, GAUGES) if g]
            topics = list(ex.map(_topic, TOPICS))
    except Exception:
        return {"gauges": [], "headlines": []}
    seen, heads = set(), []
    for res in topics:
        kept = 0
        for h in res:
            k = h["title"].lower()
            if k in seen or (now_ts - h["ts"]) / 3600 > max_age_h:
                continue
            seen.add(k); kept += 1
            heads.append({"sym": "MKT", "topic": h["topic"], "title": h["title"], "src": h["src"], "age_h": round((now_ts - h["ts"]) / 3600, 1)})
            if kept >= per_topic:
                break
    heads.sort(key=lambda h: h["age_h"])
    for i, h in enumerate(heads, 1):
        h["id"] = f"M{i}"
    return {"gauges": gauges, "headlines": heads}
