"""The "headlines" tab: collects the day's macro stories, ranks them, and (twice a day) lets the AI pick the most important
events and comment on the industries our stocks belong to. Never raises; without the AI the page shows the code-ranked list."""
import json, re
from datetime import datetime

import ai as aimod

GROUPS = {
    "semis": ["NVDA", "AVGO", "AMD", "TSM", "ARM", "MU", "MRVL", "SMCI"],
    "bigtech": ["AAPL", "MSFT", "AMZN", "GOOGL", "META"],
    "software": ["ORCL", "PLTR", "SNOW", "NOW", "PTC"],
    "auto": ["TSLA"],
    "index": ["SPY", "QQQ"],
}
TOPIC_W = {"markets": 1, "fed": 2, "politics": 2, "ai_policy": 2, "trade": 2, "world": 2, "energy": 1, "ai_credit": 3, "ai_infra": 2}
STOP = set("the a an of to in on for and or as at by with from is are was were be this that it its after amid over into than more new says say will".split())
KEEP_DAYS, TOP_N = 7, 12


def ml(x, n=200):
    """Normalise AI free text: dict {zh,en,fr} (preferred) or plain string."""
    if isinstance(x, dict):
        out = {k: str(v)[:n] for k, v in x.items() if k in ("zh", "en", "fr") and v}
        return out or {"en": ""}
    return {"en": str(x or "")[:n]}

SYS_EVENTS = """You are the news editor of a stock-market dashboard. From the numbered candidate stories (macro and market news; n_sources = how many outlets carried it) choose the MOST IMPORTANT 6 for US equities today, most important first, and comment on the industries our stocks belong to.
Use ONLY the given stories, gauges and industry statistics; never invent facts, numbers or causes. If the stories do not explain a move, say the cause is unclear. Prefer stories carried by several outlets and by major outlets; ignore clickbait and mis-dated items.
Reply with ONE JSON object only, in exactly this shape, all free text in THREE languages (en, fr, zh = Simplified Chinese), each <=140 chars:
{"mood": {"en": "one-line market mood", "fr": "...", "zh": "..."},
 "events": [{"id": "M3", "importance": 1-5, "headline": {"en": "...", "fr": "...", "zh": "..."}, "why": {"en": "why it matters for stocks / AI stocks", "fr": "...", "zh": "..."}, "affects": ["NVDA", "SOXX"]}],
 "industries": [{"group": "semis", "view": "positive|neutral|negative", "why": {"en": "...", "fr": "...", "zh": "..."}}]}
"industries" must contain every group given in industry_stats; "affects" may only use tickers/ETF names that appear in the input."""


def _tokens(title):
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 3 and w not in STOP}


def update(state, got, ts, et_day):
    """Merge one macro fetch into today's story list (merging near-duplicate headlines, counting distinct outlets)."""
    try:
        E = state.setdefault("events", {})
        E["groups"] = GROUPS
        d = E.setdefault("days", {}).setdefault(et_day, {"items": []})
        items = d["items"]
        for h in got.get("headlines", []):
            tk = _tokens(h["title"])
            hit = None
            for it in items:
                tk2 = set(it["tk"])
                if tk and tk2 and len(tk & tk2) / len(tk | tk2) >= 0.5:
                    hit = it; break
            if hit:
                if h["src"] not in hit["srcs"]:
                    hit["srcs"].append(h["src"])
                continue
            items.append({"title": h["title"], "src": h["src"], "srcs": [h["src"]], "topic": h["topic"], "url": h.get("url", ""),
                          "first": ts, "tk": sorted(tk)[:12]})
        items.sort(key=lambda it: -_score(it, ts))
        d["items"] = items[:40]
        for k in sorted(E["days"])[:-KEEP_DAYS]:
            del E["days"][k]
        for k in sorted(E.get("curated", {}))[:-KEEP_DAYS]:
            del E["curated"][k]
    except Exception:
        pass


def _score(it, ts):
    age = (datetime.fromisoformat(ts.replace("Z", "+00:00")) - datetime.fromisoformat(it["first"].replace("Z", "+00:00"))).total_seconds() / 3600
    return 2 * len(it["srcs"]) + TOPIC_W.get(it["topic"], 1) + max(0.0, 2 - age / 6)


def group_stats(market):
    out = []
    for g, syms in GROUPS.items():
        rows = [market[s] for s in syms if s in market and market[s].get("day") is not None]
        if not rows:
            continue
        best = max((s for s in syms if s in market), key=lambda s: market[s].get("day") or 0)
        worst = min((s for s in syms if s in market), key=lambda s: market[s].get("day") or 0)
        out.append({"group": g, "members": syms, "avg_today": round(sum(r["day"] for r in rows) / len(rows), 4),
                    "avg_20d": round(sum(r.get("mom20") or 0 for r in rows) / len(rows), 4),
                    "above_sma50": sum(1 for r in rows if r["price"] > r["sma50"]), "n": len(rows), "best": best, "worst": worst})
    return out


def maybe_curate(state, client, ts, et_day, hm, market, gauges):
    """At most one AI call per slot: 'am' during the session (from 10:45 ET), 'pm' after the close (from 16:05 ET)."""
    try:
        if client is None or getattr(client, "exhausted_all", False):
            return
        E = state.setdefault("events", {})
        slot = "pm" if hm >= (16, 5) else "am" if hm >= (10, 45) else None
        if slot is None:
            return
        cur = E.setdefault("curated", {}).setdefault(et_day, {})
        err = E.setdefault("errors", {})
        if slot in cur or err.get(f"{et_day}:{slot}", 0) >= 2:
            return
        if hasattr(client, "time_left") and client.time_left() < 60:
            return
        import time as _t
        if getattr(client, "last_call", 0) and _t.time() - client.last_call < 300:
            return                                          # another AI call just happened in this run: next run
        items = (E.get("days", {}).get(et_day) or {}).get("items") or []
        if not items:
            return
        cands = sorted(items, key=lambda it: -_score(it, ts))[:TOP_N]
        ids = {}
        payload_c = []
        for i, it in enumerate(cands, 1):
            ids[f"E{i}"] = it
            payload_c.append({"id": f"E{i}", "topic": it["topic"], "title": it["title"][:150], "n_sources": len(it["srcs"]), "sources": it["srcs"][:4]})
        gs = [{"name": g["name"], "today": g["d1"], "5d": g["d5"]} for g in gauges][:16]
        user = {"date": et_day, "candidates": payload_c, "gauges": gs, "industry_stats": group_stats(market)}
        messages = [{"role": "system", "content": SYS_EVENTS}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
        try:
            raw, meta = client.decide(messages, {"task": "events", **user})
            data = aimod.extract_json(raw)
        except Exception as e:
            err[f"{et_day}:{slot}"] = err.get(f"{et_day}:{slot}", 0) + 1
            E["last_error"] = f"{et_day} {slot}: {str(e)[:160]}"
            return
        evs = []
        for e in (data.get("events") or [])[:7]:
            it = ids.get(str(e.get("id")))
            if not it:
                continue
            evs.append({"id": e["id"], "importance": max(1, min(5, int(e.get("importance") or 3))), "headline": ml(e.get("headline"), 160),
                        "why": ml(e.get("why"), 200), "affects": [str(x)[:8] for x in (e.get("affects") or [])][:6],
                        "title": it["title"], "src": it["src"], "srcs": it["srcs"][:4], "url": it.get("url", "")})
        inds = [{"group": x["group"], "view": x["view"] if x.get("view") in ("positive", "neutral", "negative") else "neutral", "why": ml(x.get("why"), 200)}
                for x in (data.get("industries") or []) if x.get("group") in GROUPS]
        if not evs:
            err[f"{et_day}:{slot}"] = err.get(f"{et_day}:{slot}", 0) + 1
            return
        cur[slot] = {"t": ts, "model": meta.get("model"), "mood": ml(data.get("mood"), 160), "events": evs, "industries": inds,
                     "stats": group_stats(market)}
    except Exception:
        pass
