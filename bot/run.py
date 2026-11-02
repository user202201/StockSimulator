#!/usr/bin/env python3
"""US-stock paper trading bot. Zero dependencies (stdlib only).

Accounts (all start with the same cash, see EXPERIMENT.md):
  rule   - trend-following strategy
  spy    - buy & hold SPY (benchmark)
  equal  - buy & hold the whole universe, equal weight (benchmark, cancels survivorship bias)
  random - random decisions at a similar trade frequency (control: is the strategy better than luck?)

Realism rules enforced by the engine:
  * orders fill on the NEXT run (decision price != fill price), with slippage
  * no same-day round trip (PDT-safe), long-only, no margin, fractional shares
  * T+1 settlement: sale proceeds are usable the next trading day
  * stock splits and dividends are applied to positions
  * market session / holidays taken from Yahoo's trading-period metadata
  * stale or anomalous quotes are skipped, never traded

Usage:
  python bot/run.py            # live
  python bot/run.py --demo     # offline demo with synthetic prices
  python bot/run.py --force    # live, ignore market-open check (for testing)
"""
import json, math, os, random, sys, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.environ.get("STATE_PATH") or os.path.join(ROOT, "docs", "data", "state.json")

INITIAL_CASH = 5000.0
UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO"]
BENCH = "SPY"
MAX_POSITIONS = 4
TARGET_WEIGHT = 0.25      # of total equity per position
HARD_STOP = 0.07
TRAIL_STOP = 0.08
SLIPPAGE = 0.0005         # per side (spread / execution drift for liquid large caps)
# Fee schedule, US retail brokers, 2026 (verify yearly - see EXPERIMENT.md):
COMMISSION = 0.0          # $ per trade; mainstream US brokers are commission-free
SEC_FEE_PER_MILLION = 20.60   # SEC Section 31, sells only, $ per $1M sold (effective 2026-04-04)
TAF_PER_SHARE = 0.000195      # FINRA TAF, sells only, $ per share
TAF_MIN, TAF_MAX = 0.01, 9.79 # per trade
RANDOM_SELL_PROB = 0.06   # daily, per held position
RANDOM_BUY_PROB = 0.20    # daily, when a slot is free
STALE_SECS = 1800         # quote older than this => skip symbol
ANOMALY = 0.25            # >25% jump without a split => skip symbol
NY = ZoneInfo("America/New_York")

ACCOUNTS = {
    "rule": ("规则策略", "趋势跟随：站上50日均线且短期强于中期时买入，止损/回撤止盈/跌破均线时卖出。"),
    "spy": ("SPY 买入持有", "基准：开局全仓买入 SPY 后不再操作。"),
    "equal": ("股票池等权持有", "基准：开局把本金等分买入股票池全部标的后不再操作，用来抵消“所选股票本来就是强势股”的偏差。"),
    "random": ("随机决策", "对照：买卖频率与规则策略相近，但标的和时机完全随机，用来检验策略是否真的优于运气。"),
}


def cent_up(x):
    return math.ceil(round(x * 100, 6)) / 100

def sell_fees(shares, gross):
    """Regulatory fees charged on sales (passed through by most brokers) + commission."""
    sec = max(cent_up(gross * SEC_FEE_PER_MILLION / 1e6), 0.01)
    taf = min(max(cent_up(shares * TAF_PER_SHARE), TAF_MIN), TAF_MAX)
    return round(COMMISSION + sec + taf, 2)


# ---------- time helpers ----------
def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def et_date(ts):
    return parse_ts(ts).astimezone(NY).date().isoformat()

def next_trading_day(d):
    x = datetime.fromisoformat(d) + timedelta(days=1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x.date().isoformat()   # holidays ignored: at worst funds free up a day early


# ---------- indicators ----------
def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None

def rsi(xs, n=14):
    if len(xs) < n + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(xs[-n - 1:-1], xs[-n:]):
        d = b - a
        gains += max(d, 0); losses += max(-d, 0)
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)

def snap(c):
    f = lambda x: None if x is None else round(x, 4)
    return {"price": f(c[-1]), "sma20": f(sma(c, 20)), "sma50": f(sma(c, 50)), "rsi": f(rsi(c)),
            "mom20": f(c[-1] / c[-21] - 1) if len(c) > 20 else None,
            "mom60": f(c[-1] / c[-61] - 1) if len(c) > 60 else None}


# ---------- rule strategy ----------
def market_regime_ok(closes):
    ma = sma(closes[BENCH], 200)
    return ma is not None and closes[BENCH][-1] > ma

def entry_score(c):
    if len(c) < 61:
        return None
    p, s20, s50, r = c[-1], sma(c, 20), sma(c, 50), rsi(c)
    if p > s50 and s20 > s50 and p / c[-21] - 1 > 0 and r < 75:
        return p / c[-61] - 1
    return None

def entry_checks(c, spy, score, rank, slots):
    s20, s50, r, p = sma(c, 20), sma(c, 50), rsi(c), c[-1]
    mom20, spy200 = p / c[-21] - 1, sma(spy, 200)
    return [
        {"text": f"现价 ${p:.2f} {'>' if p > s50 else '≤'} 50日均线 ${s50:.2f}", "ok": p > s50},
        {"text": f"20日均线 ${s20:.2f} {'>' if s20 > s50 else '≤'} 50日均线 ${s50:.2f}（短期强于中期）", "ok": s20 > s50},
        {"text": f"20日动量 {mom20:+.1%}（需为正）", "ok": mom20 > 0},
        {"text": f"RSI {r:.0f}（需 < 75，避免追高）", "ok": r < 75},
        {"text": f"大盘 SPY ${spy[-1]:.2f} {'>' if spy[-1] > spy200 else '≤'} 200日均线 ${spy200:.2f}（牛市过滤）", "ok": spy[-1] > spy200},
        {"text": f"候选中 60日动量排第 {rank} 位（{score:+.1%}），剩余仓位名额 {slots}", "ok": True},
    ]

def exit_check(pos, c):
    p = c[-1]
    pos["peak"] = max(pos["peak"], p)
    hard, trail, s50 = pos["avg_cost"] * (1 - HARD_STOP), pos["peak"] * (1 - TRAIL_STOP), sma(c, 50)
    rules = [("硬止损 -7%", p <= hard, f"现价 ${p:.2f} {'≤' if p <= hard else '>'} 硬止损线 ${hard:.2f}（成本 ${pos['avg_cost']:.2f} 的 -7%）"),
             ("回撤止盈 -8%", p <= trail, f"现价 ${p:.2f} {'≤' if p <= trail else '>'} 回撤线 ${trail:.2f}（持仓最高 ${pos['peak']:.2f} 的 -8%）"),
             ("跌破50日均线", p < s50, f"现价 ${p:.2f} {'<' if p < s50 else '≥'} 50日均线 ${s50:.2f}")]
    reason = next((n for n, hit, _ in rules if hit), None)
    return reason, [{"text": txt, "ok": hit} for _, hit, txt in rules]


# ---------- account engine ----------
def new_account(name, desc):
    return {"name": name, "desc": desc, "cash": INITIAL_CASH, "unsettled": [], "positions": {},
            "pending": [], "trades": [], "equity": [], "events": [], "last_decision": None, "fees_paid": 0.0}

def new_state():
    return {"initial_cash": INITIAL_CASH, "accounts": {k: new_account(*v) for k, v in ACCOUNTS.items()},
            "prices": {}, "events_seen": {}, "updated": None, "demo": False,
            "health": {"last_ok": None, "last_attempt": None, "last_error": None, "fail_count": 0}}

def equity_of(acct, prices):
    return (acct["cash"] + sum(u["amount"] for u in acct["unsettled"]) +
            sum(p["shares"] * prices.get(s, p["avg_cost"]) for s, p in acct["positions"].items()))

def settle(acct, ts):
    today, keep = et_date(ts), []
    for u in acct["unsettled"]:
        if today >= u["avail"]:
            acct["cash"] += u["amount"]
        else:
            keep.append(u)
    acct["unsettled"] = keep

def queue(acct, side, sym, ts, price, reason, detail, dollars=None):
    acct["pending"].append({"side": side, "symbol": sym, "dollars": dollars, "reason": reason,
                            "decided_at": ts, "decision_price": round(price, 2), "detail": detail})

def exec_buy(acct, o, price, ts):
    sym = o["symbol"]
    px = price * (1 + SLIPPAGE)
    dollars = min(o["dollars"], acct["cash"])
    shares = math.floor(dollars / px * 10000) / 10000
    if shares <= 0 or sym in acct["positions"]:
        return
    before = acct["cash"]
    acct["cash"] -= shares * px
    acct["positions"][sym] = {"shares": shares, "avg_cost": px, "peak": px, "entry": ts}
    acct["trades"].append({"id": len(acct["trades"]), "time": ts, "side": "BUY", "symbol": sym, "shares": shares,
                           "price": round(px, 2), "fee": 0.0, "pnl": None, "reason": o["reason"],
                           "detail": {**o["detail"], "amount": round(shares * px, 2), "cash_before": round(before, 2),
                                      "cash_after": round(acct["cash"], 2), "decided_at": o["decided_at"],
                                      "decision_price": o["decision_price"]}})

def exec_sell(acct, o, price, ts):
    sym = o["symbol"]
    pos = acct["positions"].pop(sym)
    px = price * (1 - SLIPPAGE)
    gross = pos["shares"] * px
    fee = sell_fees(pos["shares"], gross)
    proceeds = gross - fee
    acct["fees_paid"] = round(acct.get("fees_paid", 0) + fee, 2)
    acct["unsettled"].append({"amount": proceeds, "avail": next_trading_day(et_date(ts))})
    days = (datetime.fromisoformat(et_date(ts)) - datetime.fromisoformat(et_date(pos["entry"]))).days
    cost = pos["shares"] * pos["avg_cost"]
    acct["trades"].append({"id": len(acct["trades"]), "time": ts, "side": "SELL", "symbol": sym,
                           "shares": round(pos["shares"], 4), "price": round(px, 2), "fee": fee,
                           "pnl": round(proceeds - cost, 2), "reason": o["reason"],
                           "detail": {**o["detail"], "avg_cost": round(pos["avg_cost"], 2), "peak": round(pos["peak"], 2),
                                      "entry": pos["entry"], "held_days": days, "ret": round(proceeds / cost - 1, 4),
                                      "decided_at": o["decided_at"], "decision_price": o["decision_price"]}})

def fill_pending(acct, prices, fresh, ts):
    keep = []
    for o in acct["pending"]:
        sym = o["symbol"]
        if sym not in fresh:
            keep.append(o); continue
        if o["side"] == "BUY":
            exec_buy(acct, o, prices[sym], ts)
        elif sym in acct["positions"]:
            exec_sell(acct, o, prices[sym], ts)
    acct["pending"] = keep


# ---------- decision functions (queue orders; they fill on the next run) ----------
def committed_dollars(acct):
    return sum(o["dollars"] for o in acct["pending"] if o["side"] == "BUY")

def decide_rule(acct, closes, ts, prices):
    today = et_date(ts)
    pend = {o["symbol"] for o in acct["pending"]}
    for sym, pos in list(acct["positions"].items()):
        if sym not in closes:
            continue
        r, checks = exit_check(pos, closes[sym])
        if r and sym not in pend and et_date(pos["entry"]) != today:      # no same-day round trip
            queue(acct, "SELL", sym, ts, closes[sym][-1], r, {"checks": checks, "ind": snap(closes[sym])})
    if BENCH not in closes or not market_regime_ok(closes):
        return
    pend_buys = {o["symbol"] for o in acct["pending"] if o["side"] == "BUY"}
    sold_today = {t["symbol"] for t in acct["trades"] if t["side"] == "SELL" and et_date(t["time"]) == today}
    skip = set(acct["positions"]) | pend_buys | sold_today
    cands = sorted([(entry_score(closes[s]), s) for s in UNIVERSE if s in closes and s not in skip
                    and entry_score(closes[s]) is not None], reverse=True)
    n = len(acct["positions"]) + len(pend_buys)
    for rank, (score, sym) in enumerate(cands, 1):
        if n >= MAX_POSITIONS:
            break
        eq = equity_of(acct, prices)
        dollars = min(eq * TARGET_WEIGHT, acct["cash"] - committed_dollars(acct))
        if dollars < 50:
            break
        chk = entry_checks(closes[sym], closes[BENCH], score, rank, MAX_POSITIONS - n)
        queue(acct, "BUY", sym, ts, closes[sym][-1], f"上升趋势，60日动量 {score:+.1%}",
              {"checks": chk, "ind": snap(closes[sym]), "equity": round(eq, 2), "weight": round(dollars / eq, 3)},
              dollars=dollars)
        n += 1

def _hold_forever(acct, closes, ts, syms):
    if acct["trades"] or acct["positions"] or acct["pending"] or not all(s in closes for s in syms):
        return
    each = acct["cash"] / len(syms)
    for s in syms:
        queue(acct, "BUY", s, ts, closes[s][-1], "基准：买入并持有",
              {"checks": [{"text": f"开局按 1/{len(syms)} 等权买入，之后不再操作", "ok": True}], "ind": snap(closes[s]),
               "equity": INITIAL_CASH, "weight": round(1 / len(syms), 3)}, dollars=each)

def decide_spy(acct, closes, ts, prices):
    _hold_forever(acct, closes, ts, [BENCH])

def decide_equal(acct, closes, ts, prices):
    _hold_forever(acct, closes, ts, UNIVERSE)

def decide_random(acct, closes, ts, prices):
    today = et_date(ts)
    if acct["last_decision"] == today:
        return
    acct["last_decision"] = today
    rnd = random.Random(f"seed42-{today}")       # reproducible
    pend = {o["symbol"] for o in acct["pending"]}
    for sym in sorted(acct["positions"]):
        x = rnd.random()
        if x < RANDOM_SELL_PROB and sym in closes and sym not in pend and et_date(acct["positions"][sym]["entry"]) != today:
            queue(acct, "SELL", sym, ts, closes[sym][-1], "随机抽签卖出",
                  {"checks": [{"text": f"随机数 {x:.2f} < {RANDOM_SELL_PROB}（每个持仓每天 6% 概率卖出）", "ok": True}], "ind": snap(closes[sym])})
    n = len(acct["positions"]) + sum(1 for o in acct["pending"] if o["side"] == "BUY")
    x = rnd.random()
    if n < MAX_POSITIONS and x < RANDOM_BUY_PROB:
        pool = sorted(s for s in UNIVERSE if s in closes and s not in acct["positions"] and s not in pend)
        if pool:
            sym = rnd.choice(pool)
            eq = equity_of(acct, prices)
            dollars = min(eq * TARGET_WEIGHT, acct["cash"] - committed_dollars(acct))
            if dollars >= 50:
                queue(acct, "BUY", sym, ts, closes[sym][-1], "随机抽签买入",
                      {"checks": [{"text": f"随机数 {x:.2f} < {RANDOM_BUY_PROB}（有空位时每天 20% 概率买入，标的随机选）", "ok": True}],
                       "ind": snap(closes[sym]), "equity": round(eq, 2), "weight": round(dollars / eq, 3)}, dollars=dollars)

DECIDE = {"rule": decide_rule, "spy": decide_spy, "equal": decide_equal, "random": decide_random}


def compact_equity(acct, now_ts, keep_days=7):
    """Keep full resolution for the last 7 days; older history is reduced to one point per day."""
    cutoff = (parse_ts(now_ts) - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    eq = acct["equity"]
    if not eq or eq[0]["time"] >= cutoff:
        return
    daily = {}
    for e in eq:
        if e["time"] < cutoff:
            daily[et_date(e["time"])] = e
    acct["equity"] = list(daily.values()) + [e for e in eq if e["time"] >= cutoff]

def step(state, closes, ts):
    """One run. closes: {sym: [daily closes..., live price]} for symbols with fresh, sane quotes."""
    prices = state["prices"]
    for s, c in closes.items():
        prices[s] = round(c[-1], 4)
    for aid, acct in state["accounts"].items():
        settle(acct, ts)
        fill_pending(acct, prices, set(closes), ts)
        DECIDE[aid](acct, closes, ts, prices)
        acct["equity"].append({"time": ts, "equity": round(equity_of(acct, prices), 2)})
        compact_equity(acct, ts)
    state["updated"] = ts


# ---------- corporate actions ----------
def apply_events(state, evmap, now):
    """evmap: {sym: (splits[(ts, ratio)], dividends[(ts, amount)])}. Applies only events after the position was opened."""
    for sym, (splits, divs) in evmap.items():
        seen = state["events_seen"].get(sym)
        if seen is None:
            state["events_seen"][sym] = int(now); continue
        new = sorted([(t, "S", v) for t, v in splits if seen < t <= now] + [(t, "D", v) for t, v in divs if seen < t <= now])
        for t, kind, v in new:
            for acct in state["accounts"].values():
                pos = acct["positions"].get(sym)
                if not pos or parse_ts(pos["entry"]).timestamp() >= t:
                    continue
                when = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if kind == "S":
                    pos["shares"] = round(pos["shares"] * v, 4)
                    pos["avg_cost"] /= v; pos["peak"] /= v
                    acct["events"].append({"time": when, "symbol": sym, "type": "SPLIT", "ratio": v})
                else:
                    amt = pos["shares"] * v
                    acct["cash"] += amt
                    acct["events"].append({"time": when, "symbol": sym, "type": "DIV", "amount": round(amt, 2)})
            seen = max(seen, t)
        state["events_seen"][sym] = seen


# ---------- live data ----------
def fetch(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d&events=div%2Csplits"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)["chart"]["result"][0]
    closes = [x for x in res["indicators"]["quote"][0]["close"] if x is not None]
    meta, ev = res["meta"], res.get("events") or {}
    live, rt = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if live and rt:
        same_day = (datetime.fromtimestamp(rt, NY).date() == datetime.fromtimestamp(res["timestamp"][-1], NY).date())
        if same_day:
            closes[-1] = live
        else:
            closes.append(live)
    return {"closes": closes, "meta": meta,
            "splits": [(int(v["date"]), v["numerator"] / v["denominator"]) for v in (ev.get("splits") or {}).values()],
            "divs": [(int(v["date"]), v["amount"]) for v in (ev.get("dividends") or {}).values()]}

def session_open(meta, now):
    """Open = inside Yahoo's regular trading period for today AND the quote is fresh (handles holidays/early close/halts)."""
    cp = (meta.get("currentTradingPeriod") or {}).get("regular")
    rt = meta.get("regularMarketTime")
    if not cp or not rt:
        return False
    return cp["start"] <= now < cp["end"] and now - rt <= STALE_SECS

def sane(sym, d, now):
    c, meta = d["closes"], d["meta"]
    if len(c) < 2 or now - (meta.get("regularMarketTime") or 0) > STALE_SECS:
        return f"{sym}: stale quote"
    recent_split = any(t > now - 5 * 86400 for t, _ in d["splits"])
    if abs(c[-1] / c[-2] - 1) > ANOMALY and not recent_split:
        return f"{sym}: anomalous jump {c[-1] / c[-2] - 1:+.0%}, skipped"
    return None

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return new_state()

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, separators=(",", ":"))

def run_live():
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = load_state()
    if state.get("demo") or "accounts" not in state:       # discard demo / old-format data
        state = new_state()
    h = state["health"]
    try:
        spy = fetch(BENCH)
        if not session_open(spy["meta"], now.timestamp()) and "--force" not in sys.argv:
            print("US market closed or quote stale; nothing to do."); return
        h["last_attempt"] = ts
        data, closes, evmap, notes = {BENCH: spy}, {}, {}, []
        for s in UNIVERSE:
            try:
                data[s] = data.get(s) or fetch(s)
            except Exception as e:
                notes.append(f"{s}: fetch failed ({e})")
        for s, d in data.items():
            bad = sane(s, d, now.timestamp()) if "--force" not in sys.argv else None
            if bad:
                notes.append(bad); continue
            closes[s] = d["closes"]; evmap[s] = (d["splits"], d["divs"])
        if BENCH not in closes:
            raise RuntimeError("benchmark quote unusable: " + "; ".join(notes))
        apply_events(state, evmap, now.timestamp())
        step(state, closes, ts)
        h.update(last_ok=ts, last_error="; ".join(notes) or None, fail_count=0)
        print("ok", {k: a["equity"][-1]["equity"] for k, a in state["accounts"].items()}, notes)
    except Exception as e:
        h["last_attempt"] = ts
        h["fail_count"] += 1
        h["last_error"] = f"{type(e).__name__}: {e}"[:300]
        print("run failed:", h["last_error"])
    save_state(state)


# ---------- offline demo ----------
def run_demo(days=120):
    rnd = random.Random(7)
    n = 300 + days
    series = {}
    for s in UNIVERSE:
        drift, vol = rnd.uniform(0.0002, 0.0012), rnd.uniform(0.01, 0.025)
        p, xs = 100 * rnd.uniform(0.5, 3), []
        for _ in range(n):
            p *= math.exp(drift + vol * rnd.gauss(0, 1)); xs.append(p)
        series[s] = xs
    state = new_state(); state["demo"] = True
    d, i = datetime(2026, 11, 2, tzinfo=timezone.utc) - timedelta(days=int(days * 1.45)), 300
    while i < n:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        step(state, {s: series[s][:i + 1] for s in UNIVERSE}, d.strftime("%Y-%m-%dT20:00:00Z"))
        i += 1
    state["health"].update(last_ok=state["updated"], last_attempt=state["updated"])
    save_state(state)
    for k, a in state["accounts"].items():
        print(f"demo {k}: equity={a['equity'][-1]['equity']} trades={len(a['trades'])}")

if __name__ == "__main__":
    run_demo() if "--demo" in sys.argv else run_live()
