"""Learning features: daily digest + lesson, trade post-mortems, news-impact study, signal log with outcomes.

Everything here is *observational*: it never influences a trading decision, and every failure is swallowed so the
trading loop can never break because of it. All free text is requested in three languages (en, fr, zh).
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import ai as aimod

NY = ZoneInfo("America/New_York")
TRADERS = ("rule_long", "rule_short", "ai_long", "ai_short", "ai_aggr")
EN_NAME = {"rule_long": "A trend rules (swing)", "rule_short": "B intraday rules", "ai_long": "C AI swing", "ai_short": "D AI short-term", "ai_aggr": "E AI aggressive high-turnover (instant settlement)",
           "spy": "SPY buy&hold", "equal": "equal-weight 21", "random": "random control"}
TOPICS = ["sma", "rsi", "momentum", "drawdown", "volatility", "stop", "slippage", "t1", "pdt", "fractional", "benchmark",
          "survivorship", "lookahead", "samplesize", "winrate", "turnover", "fees", "regime", "relstrength", "calibration"]
NEWS_TYPES = ("earnings", "product", "analyst", "regulatory", "mna", "macro", "legal", "other")
VERDICTS = ("good_call", "bad_call", "lucky", "unlucky", "neutral")
MAX_CALLS = 5                      # learning calls per day, on top of the trading calls
REASON_EN = {"r.hard_stop": "hard stop", "r.trail_stop": "volatility-based trailing stop", "r.below_sma50": "fell below the 50-day average",
             "r.uptrend": "uptrend entry (60-day momentum)", "r.tp": "take profit +1%", "r.sl": "stop loss -0.8%", "r.flatten": "flatten before the close", "r.eod_cut": "end-of-day cut: not in profit",
             "r.dip": "intraday dip buy", "r.brk": "intraday momentum-continuation buy", "r.bh": "buy and hold", "r.rnd_buy": "random buy", "r.rnd_sell": "random sell",
             "r.ai_buy": "AI buy", "r.ai_sell": "AI sell", "r.code_stop": "code stop-loss", "r.code_flatten": "code flatten before the close"}

SYS_DIGEST = """You are a friendly market educator writing a daily learning note for a beginner who follows a PAPER-trading simulation (4 virtual traders: 2 rule-based, 2 AI; plus 3 control accounts). Use ONLY the data in the user message; never invent facts, prices or news. This is education, not investment advice. Write every free-text field in THREE languages (en, fr, zh = Simplified Chinese), plain-spoken, explaining jargon briefly.
Return ONE JSON object, no markdown:
{"headline": {"en": "...", "fr": "...", "zh": "..."},            // the day in one line, <=90 chars
 "bullets": [{"en": "...", "fr": "...", "zh": "..."}],           // 3 to 5 items, each <=160 chars: what moved the market and why
 "movers": [{"symbol": "AMD", "why": {"en": "...", "fr": "...", "zh": "..."}}],   // up to 4 biggest movers; explain from the headlines, or say there is no clear news
 "lesson": {"topic": "<one id from topics>", "title": {"en": "...", "fr": "...", "zh": "..."}, "body": {"en": "...", "fr": "...", "zh": "..."}}}   // body <=350 chars: teach this concept with TODAY's real example"""
SYS_POST = """You review closed PAPER trades for a beginner. Judge DECISION quality, not just profit: a loss after a sound, rule-following decision is 'unlucky'; a win that came from a market-wide jump or contradicts the rules is 'lucky'. Use only the data given. Educational, not advice.
Return ONE JSON object: {"reviews": [{"key": "<key from input>", "verdict": "good_call|bad_call|lucky|unlucky|neutral", "review": {"en": "...", "fr": "...", "zh": "..."}, "takeaway": {"en": "...", "fr": "...", "zh": "..."}}]}
review <=220 chars (what happened and why), takeaway <=140 chars (one lesson). zh = Simplified Chinese."""
SYS_NEWS = """Classify each stock headline using only its text. Return ONE JSON object: {"items": [{"id": "N3", "type": "earnings|product|analyst|regulatory|mna|macro|legal|other", "sent": -1|0|1, "imp": 1|2|3}]}
sent = effect on that company's stock (-1 negative, 0 neutral/unclear, 1 positive). imp = importance 1 (minor) to 3 (major)."""


def et_date(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(NY).date().isoformat()

def ml(x, n=300):
    if isinstance(x, dict):
        out = {k: str(v)[:n] for k, v in x.items() if k in ("zh", "en", "fr") and v}
        return out or {"en": ""}
    return {"en": str(x or "")[:n]}

def reason_en(r):
    if isinstance(r, dict) and "k" in r:
        base = REASON_EN.get(r["k"], r["k"])
        why = (r.get("a") or {}).get("why")
        return f"{base}: {ml(why).get('en', '')}" if why else base
    return str(r)[:80]

def _L(state):
    L = state.setdefault("learn", {})
    for k, v in (("journal", []), ("reviews", {}), ("news", []), ("calls", {}), ("done", {}), ("errors", {}), ("last_error", None)):
        L.setdefault(k, v)
    return L


# ---------------- signals and outcomes ----------------
def record_signal(state, aid, sym, action, conf, applied, px, ts):
    sig = state.setdefault("signals", [])
    seq = state["signal_seq"] = state.get("signal_seq", 0) + 1
    sig.append({"id": seq, "time": ts, "date": et_date(ts), "acct": aid, "sym": sym, "action": action, "conf": round(conf, 2),
                "applied": bool(applied), "px": round(px, 2), "r1": None, "r5": None})
    del sig[:-1000]

def store_daily_close(state, today):
    dc = state.setdefault("daily_close", {})
    dc[today] = {s: round(p, 2) for s, p in state.get("prices", {}).items()}
    for k in sorted(dc)[:-60]:
        del dc[k]

def update_outcomes(state):
    dc = state.get("daily_close", {})
    keys = sorted(dc)

    def nth(date, n):
        later = [k for k in keys if k > date]
        return later[n - 1] if len(later) >= n else None

    for s in state.get("signals", []):
        for fld, n in (("r1", 1), ("r5", 5)):
            if s[fld] is None:
                k = nth(s["date"], n)
                if k and s["sym"] in dc[k] and s["px"]:
                    s[fld] = round(dc[k][s["sym"]] / s["px"] - 1, 4)
    for e in _L(state)["news"]:
        base = dc.get(e["date"], {}).get(e["sym"]) or e.get("px0")
        for fld, n in (("r1", 1), ("r5", 5)):
            if e[fld] is None and base:
                k = nth(e["date"], n)
                if k and e["sym"] in dc[k]:
                    e[fld] = round(dc[k][e["sym"]] / base - 1, 4)


# ---------------- the daily post-close pass ----------------
def _ask(state, client, task, system, user_obj, today):
    L = _L(state)
    if L["calls"].get(today, 0) >= MAX_CALLS or L["errors"].get(f"{today}:{task}", 0) >= 2:
        return None
    L["calls"][today] = L["calls"].get(today, 0) + 1
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)}]
    try:
        raw, meta = client.decide(messages, {"task": task, **user_obj})
        return aimod.extract_json(raw), meta
    except Exception as e:
        L["errors"][f"{today}:{task}"] = L["errors"].get(f"{today}:{task}", 0) + 1
        L["last_error"] = f"{today} {task}: {str(e)[:200]}"
        return None

def _closed_today(state, today):
    out = []
    for aid in TRADERS:
        a = state["accounts"].get(aid)
        if not a:
            continue
        for t in a["trades"]:
            if t["side"] == "SELL" and et_date(t["time"]) == today and f"{aid}:{t['id']}" not in _L(state)["reviews"]:
                buy = next((b for b in reversed(a["trades"]) if b["side"] == "BUY" and b["symbol"] == t["symbol"] and b["id"] < t["id"]), None)
                spy_now, spy0 = state["prices"].get("SPY"), (buy or {}).get("detail", {}).get("spy_px")
                out.append({"key": f"{aid}:{t['id']}", "aid": aid, "trader": EN_NAME[aid], "symbol": t["symbol"],
                            "buy_reason": reason_en(buy["reason"]) if buy else "?", "sell_reason": reason_en(t["reason"]),
                            "return_pct": round((t.get("detail") or {}).get("ret", 0) * 100, 2), "pnl_usd": t["pnl"],
                            "held_days": (t.get("detail") or {}).get("held_days"),
                            "spy_move_since_buy_pct": round((spy_now / spy0 - 1) * 100, 2) if spy_now and spy0 else None,
                            "ai_view_at_buy": (ml(((buy or {}).get("detail") or {}).get("ai", {}).get("summary", "")).get("en", "") or None)})
    return out[:10]

def post_close(state, client, ts, news_fn, names, universe):
    """Runs after the close. Always records the day's closes and updates outcomes; asks the AI (if any) for the learning content."""
    try:
        today = et_date(ts)
        store_daily_close(state, today)
        update_outcomes(state)
        L = _L(state)
        for k in sorted(L["done"])[:-30]:
            del L["done"][k]
        if client is None:
            return
        done = L["done"].setdefault(today, [])
        now = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        cache = {}

        def headlines():
            if "n" not in cache:
                cache["n"] = [n for n in (news_fn() or []) if n["sym"] in universe and now - n["ts"] <= 24 * 3600]
            return cache["n"]

        # 1) news tagging  (feeds the "what moves prices" study)
        if "newstag" not in done:
            seen = {e["title"].lower() for e in L["news"]}
            items = [n for n in headlines() if n["title"].lower() not in seen][:50]
            if not items:
                done.append("newstag")
            else:
                r = _ask(state, client, "newstag", SYS_NEWS, {"headlines": [{"id": f"N{i + 1}", "sym": n["sym"], "title": n["title"][:140]} for i, n in enumerate(items)]}, today)
                if r:
                    by = {str(x.get("id")): x for x in (r[0].get("items") or [])}
                    for i, n in enumerate(items):
                        x = by.get(f"N{i + 1}")
                        if not x:
                            continue
                        typ = str(x.get("type", "other")).lower()
                        try:
                            sent = max(-1, min(1, int(x.get("sent", 0)))); imp = max(1, min(3, int(x.get("imp", 1))))
                        except Exception:
                            sent, imp = 0, 1
                        L["news"].append({"date": today, "sym": n["sym"], "title": n["title"][:160], "src": n["src"], "type": typ if typ in NEWS_TYPES else "other",
                                          "sent": sent, "imp": imp, "px0": state["prices"].get(n["sym"]), "r1": None, "r5": None})
                    del L["news"][:-800]
                    done.append("newstag")
        # 2) post-mortems of today's closed trades
        if "postmortem" not in done:
            closed = _closed_today(state, today)
            if not closed:
                done.append("postmortem")
            else:
                r = _ask(state, client, "postmortem", SYS_POST, {"trades": [{k: v for k, v in c.items() if k != "aid"} for c in closed]}, today)
                if r:
                    meta_by = {c["key"]: c for c in closed}
                    for x in (r[0].get("reviews") or []):
                        c = meta_by.get(str(x.get("key")))
                        if not c:
                            continue
                        v = str(x.get("verdict", "neutral")).lower()
                        L["reviews"][c["key"]] = {"date": today, "acct": c["aid"], "sym": c["symbol"], "ret": c["return_pct"] / 100, "pnl": c["pnl_usd"],
                                                  "verdict": v if v in VERDICTS else "neutral", "review": ml(x.get("review"), 260), "takeaway": ml(x.get("takeaway"), 180)}
                    done.append("postmortem")
                    if len(L["reviews"]) > 400:                       # keep the file small
                        for k in sorted(L["reviews"], key=lambda k: L["reviews"][k]["date"])[:-400]:
                            del L["reviews"][k]
        # 3) the digest: recap + lesson
        if "digest" not in done:
            mk = state.get("market", {})
            movers = sorted(({"symbol": s, "day_pct": round(m["day"] * 100, 2), "rsi": m["rsi"], "mom60_pct": round(m["mom60"] * 100, 1)} for s, m in mk.items()),
                            key=lambda x: -abs(x["day_pct"]))[:8]
            ai_avg = [m["day"] for s, m in mk.items() if s in ("AMD", "PLTR", "TSM", "ORCL", "ARM", "MU", "MRVL", "SMCI", "SNOW", "NOW")]
            accts = []
            for aid in list(TRADERS) + ["spy", "equal", "random"]:
                a = state["accounts"].get(aid)
                if a:
                    accts.append({"account": EN_NAME[aid], "total_return_pct": round((a["equity"][-1]["equity"] / state["initial_cash"] - 1) * 100, 2),
                                  "holds": [{"symbol": s, "since_buy_pct": round((state["prices"].get(s, p["avg_cost"]) / p["avg_cost"] - 1) * 100, 2)} for s, p in a["positions"].items()][:6]})
            trades_today = [{"trader": EN_NAME[aid], "side": t["side"], "symbol": t["symbol"], "reason": reason_en(t["reason"])}
                            for aid in TRADERS if state["accounts"].get(aid) for t in state["accounts"][aid]["trades"] if et_date(t["time"]) == today][:20]
            hl = headlines()[:30]
            r = _ask(state, client, "digest", SYS_DIGEST, {
                "date": today, "spy_day_pct": round(mk.get("SPY", {}).get("day", 0) * 100, 2), "qqq_day_pct": round(mk.get("QQQ", {}).get("day", 0) * 100, 2),
                "ai_stocks_avg_day_pct": round(sum(ai_avg) / len(ai_avg) * 100, 2) if ai_avg else None, "movers": movers, "accounts": accts,
                "trades_today": trades_today, "headlines": [{"id": f"N{i + 1}", "sym": n["sym"], "title": n["title"][:140]} for i, n in enumerate(hl)],
                "topics": TOPICS}, today)
            if r:
                d, meta = r
                lesson = d.get("lesson") or {}
                topic = str(lesson.get("topic", "")).lower()
                entry = {"date": today, "headline": ml(d.get("headline"), 120), "bullets": [ml(b, 220) for b in (d.get("bullets") or [])[:5]],
                         "movers": [{"symbol": str(m.get("symbol", "")).upper(), "why": ml(m.get("why"), 220)} for m in (d.get("movers") or [])[:4] if str(m.get("symbol", "")).upper() in universe],
                         "lesson": {"topic": topic if topic in TOPICS else "samplesize", "title": ml(lesson.get("title"), 90), "body": ml(lesson.get("body"), 420)},
                         "model": meta.get("model")}
                L["journal"] = [j for j in L["journal"] if j["date"] != today] + [entry]
                del L["journal"][:-60]
                done.append("digest")
    except Exception as e:                       # learning must never break the trading loop
        try:
            _L(state)["last_error"] = f"post_close: {type(e).__name__}: {str(e)[:160]}"
        except Exception:
            pass
