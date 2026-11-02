#!/usr/bin/env python3
"""US-stock paper trading bot. Zero dependencies (stdlib only).

Usage:
  python bot/run.py            # live: fetch Yahoo data, decide, update docs/data/state.json
  python bot/run.py --demo     # offline demo: synthetic prices, simulate ~120 trading days
"""
import json, math, os, random, sys, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "docs", "data", "state.json")

INITIAL_CASH = 5000.0
UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO"]
BENCH = "SPY"
MAX_POSITIONS = 4
TARGET_WEIGHT = 0.25      # of total equity per position
HARD_STOP = 0.07          # sell if -7% from cost
TRAIL_STOP = 0.08         # sell if -8% from peak since entry
SLIPPAGE = 0.0005         # 0.05% each side; US brokers are commission-free
NY = ZoneInfo("America/New_York")


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
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


# ---------- strategy ----------
def market_regime_ok(closes):
    """Only open new positions when SPY is above its 200-day average."""
    ma = sma(closes[BENCH], 200)
    return ma is not None and closes[BENCH][-1] > ma

def entry_score(c):
    """Trend-following: price>SMA50, SMA20>SMA50, positive momentum, not overbought.
    Returns a ranking score (60d momentum) or None."""
    if len(c) < 61:
        return None
    p, s20, s50, r = c[-1], sma(c, 20), sma(c, 50), rsi(c)
    mom20, mom60 = p / c[-21] - 1, p / c[-61] - 1
    if p > s50 and s20 > s50 and mom20 > 0 and r < 75:
        return mom60
    return None

def snap(c):
    f = lambda x: None if x is None else round(x, 4)
    return {"price": f(c[-1]), "sma20": f(sma(c, 20)), "sma50": f(sma(c, 50)), "rsi": f(rsi(c)),
            "mom20": f(c[-1] / c[-21] - 1) if len(c) > 20 else None,
            "mom60": f(c[-1] / c[-61] - 1) if len(c) > 60 else None}

def exit_check(pos, c):
    """Returns (reason or None, checks). Also updates the position's peak."""
    p = c[-1]
    pos["peak"] = max(pos["peak"], p)
    hard, trail, s50 = pos["avg_cost"] * (1 - HARD_STOP), pos["peak"] * (1 - TRAIL_STOP), sma(c, 50)
    rules = [("硬止损 -7%", p <= hard, f"现价 ${p:.2f} {'≤' if p <= hard else '>'} 硬止损线 ${hard:.2f}（成本 ${pos['avg_cost']:.2f} 的 -7%）"),
             ("回撤止盈 -8%", p <= trail, f"现价 ${p:.2f} {'≤' if p <= trail else '>'} 回撤线 ${trail:.2f}（持仓最高 ${pos['peak']:.2f} 的 -8%）"),
             ("跌破50日均线", p < s50, f"现价 ${p:.2f} {'<' if p < s50 else '≥'} 50日均线 ${s50:.2f}")]
    checks = [{"text": txt, "ok": hit} for _, hit, txt in rules]
    reason = next((n for n, hit, _ in rules if hit), None)
    return reason, checks

def entry_checks(c, spy, score, rank, slots):
    s20, s50, r = sma(c, 20), sma(c, 50), rsi(c)
    mom20 = c[-1] / c[-21] - 1
    p = c[-1]
    return [
        {"text": f"现价 ${p:.2f} {'>' if p > s50 else '≤'} 50日均线 ${s50:.2f}", "ok": p > s50},
        {"text": f"20日均线 ${s20:.2f} {'>' if s20 > s50 else '≤'} 50日均线 ${s50:.2f}（短期强于中期）", "ok": s20 > s50},
        {"text": f"20日动量 {mom20:+.1%}（需为正）", "ok": mom20 > 0},
        {"text": f"RSI {r:.0f}（需 < 75，避免追高）", "ok": r < 75},
        {"text": f"大盘 SPY ${spy[-1]:.2f} {'>' if spy[-1] > sma(spy, 200) else '≤'} 200日均线 ${sma(spy, 200):.2f}（牛市过滤）", "ok": spy[-1] > sma(spy, 200)},
        {"text": f"候选中 60日动量排第 {rank} 位（{score:+.1%}），剩余仓位名额 {slots}", "ok": True},
    ]


# ---------- engine ----------
def new_state():
    return {"initial_cash": INITIAL_CASH, "cash": INITIAL_CASH, "positions": {},
            "trades": [], "equity": [], "prices": {}, "updated": None, "demo": False}

def equity_of(state, prices):
    return state["cash"] + sum(p["shares"] * prices[s] for s, p in state["positions"].items())

def sell(state, sym, price, ts, reason, c, checks):
    pos = state["positions"].pop(sym)
    px = price * (1 - SLIPPAGE)
    state["cash"] += pos["shares"] * px
    days = (datetime.fromisoformat(ts[:10]) - datetime.fromisoformat(pos["entry"][:10])).days
    state["trades"].append({"id": len(state["trades"]), "time": ts, "side": "SELL", "symbol": sym,
                            "shares": round(pos["shares"], 4), "price": round(px, 2),
                            "pnl": round(pos["shares"] * (px - pos["avg_cost"]), 2), "reason": reason,
                            "detail": {"avg_cost": round(pos["avg_cost"], 2), "peak": round(pos["peak"], 2),
                                       "entry": pos["entry"], "held_days": days,
                                       "ret": round(px / pos["avg_cost"] - 1, 4),
                                       "checks": checks, "ind": snap(c)}})

def buy(state, sym, price, ts, dollars, reason, c, checks, extra):
    px = price * (1 + SLIPPAGE)
    shares = math.floor(dollars / px * 10000) / 10000
    if shares <= 0:
        return
    eq_before = state["cash"]
    state["cash"] -= shares * px
    state["positions"][sym] = {"shares": shares, "avg_cost": px, "peak": px, "entry": ts}
    state["trades"].append({"id": len(state["trades"]), "time": ts, "side": "BUY", "symbol": sym,
                            "shares": shares, "price": round(px, 2), "pnl": None, "reason": reason,
                            "detail": {"amount": round(shares * px, 2), "cash_before": round(eq_before, 2),
                                       "cash_after": round(state["cash"], 2), "checks": checks,
                                       "ind": snap(c), **extra}})

def step(state, closes, ts, trade=True):
    """closes: {sym: [daily closes..., latest price]}. One decision round."""
    prices = {s: c[-1] for s, c in closes.items()}
    if trade:
        today = ts[:10]
        for sym in list(state["positions"]):
            r, checks = exit_check(state["positions"][sym], closes[sym])
            if r:
                sell(state, sym, prices[sym], ts, r, closes[sym], checks)
        if market_regime_ok(closes):
            held = state["positions"]
            sold_today = {t["symbol"] for t in state["trades"] if t["time"][:10] == today and t["side"] == "SELL"}
            cands = [(entry_score(closes[s]), s) for s in UNIVERSE
                     if s not in held and s not in sold_today and s in closes]
            cands = sorted([c for c in cands if c[0] is not None], reverse=True)
            for rank, (score, sym) in enumerate(cands, 1):
                if len(state["positions"]) >= MAX_POSITIONS:
                    break
                eq = equity_of(state, prices)
                dollars = min(eq * TARGET_WEIGHT, state["cash"])
                if dollars >= 50:
                    slots = MAX_POSITIONS - len(state["positions"])
                    chk = entry_checks(closes[sym], closes[BENCH], score, rank, slots)
                    buy(state, sym, prices[sym], ts, dollars, f"上升趋势，60日动量 {score:+.1%}", closes[sym], chk,
                        {"equity": round(eq, 2), "weight": round(dollars / eq, 3)})
    eq = equity_of(state, prices)
    spy_ret = prices[BENCH]
    state["prices"] = {k: round(v, 2) for k, v in prices.items()}
    state["equity"].append({"time": ts, "equity": round(eq, 2), "bench": round(spy_ret, 2)})
    state["updated"] = ts


# ---------- live data ----------
def fetch_closes(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)["chart"]["result"][0]
    closes = [x for x in res["indicators"]["quote"][0]["close"] if x is not None]
    live = res["meta"].get("regularMarketPrice")
    if live:
        # last daily bar is today's partial bar when market is open: replace it with the live price
        today = datetime.fromtimestamp(res["meta"]["regularMarketTime"], NY).date()
        last_bar = datetime.fromtimestamp(res["timestamp"][-1], NY).date()
        if last_bar == today:
            closes[-1] = live
        else:
            closes.append(live)
    return closes

def market_open(now=None):
    now = (now or datetime.now(NY)).astimezone(NY)
    return now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return new_state()

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state["equity"] = state["equity"][-3000:]
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=1)

def run_live():
    if not market_open() and "--force" not in sys.argv:
        print("US market closed; nothing to do."); return
    state = load_state()
    if state.get("demo"):          # discard demo data on first real run
        state = new_state()
    closes = {}
    for s in UNIVERSE:
        try:
            closes[s] = fetch_closes(s)
        except Exception as e:
            print(f"fetch {s} failed: {e}")
    if BENCH not in closes:
        sys.exit("benchmark data unavailable; skipping this round")
    step(state, closes, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    save_state(state)
    print(f"equity={state['equity'][-1]['equity']} cash={state['cash']:.2f} positions={list(state['positions'])}")


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
    start = datetime(2026, 11, 2, tzinfo=timezone.utc) - timedelta(days=int(days * 1.45))
    d, i = start, 300
    while i < n:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        closes = {s: series[s][:i + 1] for s in UNIVERSE}
        step(state, closes, d.strftime("%Y-%m-%dT20:00:00Z"))
        i += 1
    save_state(state)
    print(f"demo equity={state['equity'][-1]['equity']} trades={len(state['trades'])}")

if __name__ == "__main__":
    run_demo() if "--demo" in sys.argv else run_live()
