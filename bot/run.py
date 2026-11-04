#!/usr/bin/env python3
"""US-stock paper trading experiment. Stdlib only.

Seven accounts, same start ($5,000), same data, same fill/fee rules (see EXPERIMENT.md):
  rule_long  A  rules, trend following, holds days-weeks
  rule_short B  rules, intraday dip-buying, flat before the close
  ai_long    C  AI + headlines, one review per day, swing holding
  ai_short   D  AI + headlines, 4 reviews per day, intraday, flat before the close
  spy / equal / random   controls

Realism enforced by the engine (never by the AI): fills on the NEXT run with slippage, regulatory sell fees,
T+1 settlement (buys use settled cash only = cash-account model, no PDT), splits/dividends, market-session check,
stale / anomalous quote protection, long-only, fractional shares.

  python bot/run.py          live      python bot/run.py --demo   offline demo with synthetic data
"""
import copy, json, math, os, random, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai as aimod
import news as newsmod
import learn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.environ.get("STATE_PATH") or os.path.join(ROOT, "docs", "data", "state.json")
STATE_VERSION = 3
DEMO = False

INITIAL_CASH = 5000.0
CORE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO"]
AI_STOCKS = ["AMD", "PLTR", "TSM", "ORCL", "ARM", "MU", "MRVL", "SMCI", "SNOW", "NOW"]
UNIVERSE = CORE + AI_STOCKS + ["PTC"]
NAMES = {"SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ", "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia",
         "AMZN": "Amazon", "GOOGL": "Alphabet Google", "META": "Meta Platforms", "TSLA": "Tesla", "AVGO": "Broadcom",
         "AMD": "Advanced Micro Devices", "PLTR": "Palantir", "TSM": "Taiwan Semiconductor", "ORCL": "Oracle",
         "ARM": "Arm Holdings", "MU": "Micron", "MRVL": "Marvell", "SMCI": "Super Micro Computer",
         "SNOW": "Snowflake", "NOW": "ServiceNow", "PTC": "PTC Inc"}
BENCH = "SPY"

SLIPPAGE = 0.0005
COMMISSION = 0.0
SEC_FEE_PER_MILLION = 20.60     # sells only, effective 2026-04-04
TAF_PER_SHARE = 0.000195        # sells only
TAF_MIN, TAF_MAX = 0.01, 9.79
STALE_SECS, ANOMALY = 1800, 0.25
NY = ZoneInfo("America/New_York")

# rule_long (A)
A_MAX_POS, A_WEIGHT, A_HARD_STOP, A_TRAIL = 4, 0.25, 0.07, 0.08
A_VOL_TARGET, A_W_MIN, A_W_MAX = 0.004, 0.10, 0.30   # A: volatility-targeted sizing: weight = target / daily vol, clamped (calmer stocks get more)
# rule_short (B)
B_MAX_POS, B_WEIGHT, B_DIP, B_TP, B_SL = 4, 0.20, 0.012, 0.010, 0.008
B_VOL_TARGET, B_W_MIN, B_W_MAX = 0.003, 0.08, 0.25   # B: same idea, smaller
INTRADAY_WINDOW = ((10, 0), (15, 30))    # B: new entries only inside this window
EOD_AT = (15, 30)                        # B: end-of-day check (orders queued now still fill at the 15:45 run)
B_CARRY_MIN = 0.0                        # B: at the EOD check, positions in profit are carried overnight; the rest are sold
# random control
RANDOM_SELL_PROB, RANDOM_BUY_PROB = 0.10, 0.50   # per held position / per free slot, per day (v2: keeps exposure and trade rate close to the real traders)
RANDOM_VER = 2

NO_LIMIT = 99                     # "no fixed limit" for the AI traders; cap 1.0 = no per-stock limit (only the available cash limits a position)
E_DEFAULT_CAP, E_RESERVE = 60, 8  # E (aggressive) may use AI calls until the day's total reaches this, or (observed daily limit - reserve)
AI_PARAMS = {
    "ai_long": {"horizon": "long", "max_pos": NO_LIMIT, "cap": 1.0, "stop": 0.10, "min_hold": 2, "max_buys": NO_LIMIT,
                "rounds": [(10, 0)], "news_age": 36, "flatten": None, "no_new_after": None, "reentry": True},
    "ai_short": {"horizon": "short", "max_pos": NO_LIMIT, "cap": 1.0, "stop": 0.03, "min_hold": 0, "max_buys": NO_LIMIT,
                 "rounds": [(10, 0), (11, 30), (13, 0), (14, 30), (15, 30)], "news_age": 8, "flatten": None, "no_new_after": (15, 45), "reentry": True},
    "ai_aggr": {"horizon": "aggr", "max_pos": NO_LIMIT, "cap": 1.0, "stop": None, "min_hold": 0, "max_buys": NO_LIMIT,
                "rounds": [(h, m) for h in range(10, 16) for m in (0, 30)], "news_age": 8, "flatten": None, "no_new_after": (15, 45),
                "reentry": True, "budget": True},
}

ACCOUNTS = {
    "rule_long": dict(name="A 趋势规则", kind="rule_long", style="规则 · 中线趋势", freq="每 15 分钟检查，持有数天到数周",
        desc="完全按预设条件买卖，没有 AI。顺势而为：股价站上 50 日均线、短期强于中期、动量为正时买入。",
        rules=["买入：现价>50日均线，20日均线>50日均线，20日动量>0，RSI<75，且 SPY 在 200 日均线之上",
               "最多同时持有 4 只，按 60 日动量排序选股；仓位按波动率调整（波动越大买得越少，每只 10%–30%），不是平均分配",
               "卖出：亏损 7% 止损 / 从最高点回撤 8% / 跌破 50 日均线",
               "同一天买入的股票当天不卖，卖出后当天不再买回"]),
    "rule_short": dict(name="B 日内规则", kind="rule_short", style="规则 · 短线", freq="每 15 分钟检查，可一天多次交易，有盈利的可留仓过夜",
        desc="完全按预设条件做短线：趋势向上的股票盘中回调时低吸，涨一点就卖；收盘前把没盈利的卖掉，有盈利的留过夜。没有 AI。",
        rules=["买入：10:00–15:30 之间，上升趋势股票（现价>50日均线且20日均线>50日均线）较今日开盘价回落 ≥1.2%，且 SPY 当日跌幅没有超过 1.5%",
               "最多同时持有 4 只，仓位按波动率调整（每只 8%–25%），每只股票每天最多交易一次",
               "卖出：盈利 +1.0% 止盈 / 亏损 -0.8% 止损 / 15:30 收盘前检查：没有盈利的卖出，有盈利的留仓过夜"]),
    "ai_long": dict(name="C AI 稳健", kind="ai_long", style="AI + 新闻 · 中线", freq="每天 1 次（美东 10:00），持有数天到数周",
        desc="每天开盘半小时后，AI 读取 21 只股票的行情指标和最新新闻标题，自己判断买什么、卖什么、仓位多少。",
        rules=["AI 自主决策，必须引用新闻编号，买入前必须先写看多和看空理由",
               "代码只保留基本风控：买入后至少持有 2 天，亏损 10% 强制止损；不限制持仓只数、选股和单只仓位（可以集中持有一只，也可以分散）",
               "资金使用（软性要求，不强制）：波段投资者通常保持 70%–90% 仓位，现金超过 30% 时 AI 需要说明理由"]),
    "ai_short": dict(name="D AI 短线", kind="ai_short", style="AI + 新闻 · 短线", freq="每天 5 次（美东 10:00 / 11:30 / 13:00 / 14:30 / 15:30 收盘复盘），可隔夜持有",
        desc="一天内多次复盘，AI 结合当天走势和新闻做短线交易；15:30 的收盘复盘由 AI 决定每个仓位留过夜还是卖出，也可以根据当天新闻在收盘前追加买入。",
        rules=["AI 自主决策，必须引用新闻编号，买入前必须先写看多和看空理由",
               "代码只保留基本风控：亏损 3% 强制止损，15:45 后不开新仓；不限制持仓只数、选股和单只仓位（可以集中持有一只，也可以分散），按操盘手惯例自行保留一定现金"]),
    "ai_aggr": dict(name="E AI 激进", kind="ai_aggr", style="AI + 新闻 · 高周转", freq="每 30 分钟复盘（美东 10:00–15:30，约 12 次，受免费 AI 额度限制），可隔夜持有",
        desc="从 D 克隆、更激进的版本：目标是提高资金周转率，让资金尽量一直在运转，用来测试“高周转”是否更有效率。买卖次数、持仓只数都不设限，只受免费 AI 额度限制。",
        rules=["AI 自主决策，必须引用新闻编号；看多/看空辩论对它是可选的（为了速度）",
               "没有持仓数量限制、没有强制止损、没有每轮买入次数限制、没有单只仓位上限；只保留：只做多、不加杠杆、不能超过可用资金",
               "实验设定：卖出的钱立即可以再买，也不受 PDT 限制（相当于保证金账户的日内交易条件，但不加杠杆）——这和其他账户不同，对比时请注意",
               "每天约 12 次复盘，受免费 AI 额度限制；额度用尽时自动暂停当天的复盘"]),
    "spy": dict(name="SPY 买入持有", kind="spy", style="对照 · 基准", freq="只在开局买入一次",
        desc="基准：开局全仓买入 SPY 后不再操作。", rules=[]),
    "equal": dict(name="股票池等权持有", kind="equal", style="对照 · 基准", freq="只在开局买入一次",
        desc="基准：开局把本金等分买入全部 21 只股票后不再操作，用来抵消“所选股票本来就是强势股”的偏差。", rules=[]),
    "random": dict(name="随机决策", kind="random", style="对照 · 随机", freq="每天抽签一次",
        desc="对照：买卖完全随机，用来检验各交易员是否真的优于运气。", rules=["每个持仓每天 10% 概率卖出；每个空位每天 50% 概率随机买入一只，仓位在 10%–30% 之间随机（最多同时持有 4 只，与交易员的持仓水平接近）"]),
}


# ---------- helpers ----------
def K(k, **a):
    """Translatable text: the dashboard renders key k with args a in the viewer's language."""
    return {"k": k, "a": a}

def ml(x, n=200):
    """Normalise AI free text: dict {zh,en,fr} (preferred) or plain string."""
    if isinstance(x, dict):
        out = {k: str(v)[:n] for k, v in x.items() if k in ("zh", "en", "fr") and v}
        return out or {"en": ""}
    return {"en": str(x or "")[:n]}

def cent_up(x):
    return math.ceil(round(x * 100, 6)) / 100

def sell_fees(shares, gross):
    sec = max(cent_up(gross * SEC_FEE_PER_MILLION / 1e6), 0.01)
    taf = min(max(cent_up(shares * TAF_PER_SHARE), TAF_MIN), TAF_MAX)
    return round(COMMISSION + sec + taf, 2)

def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def et_date(ts):
    return parse_ts(ts).astimezone(NY).date().isoformat()

def et_hm(ts):
    d = parse_ts(ts).astimezone(NY)
    return (d.hour, d.minute)

def next_trading_day(d):
    x = datetime.fromisoformat(d) + timedelta(days=1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x.date().isoformat()

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

def vol20(c):
    """Daily return volatility over the last 20 closes."""
    rets = [c[k] / c[k - 1] - 1 for k in range(len(c) - 20, len(c))]
    mu = sum(rets) / len(rets)
    return (sum((x - mu) ** 2 for x in rets) / len(rets)) ** 0.5

def vol_weight(c, target, lo, hi):
    s = max(vol20(c), 1e-4)
    return min(max(target / s, lo), hi), s

def uptrend(c):
    return len(c) >= 51 and c[-1] > sma(c, 50) and sma(c, 20) > sma(c, 50)


# ---------- rule A ----------
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
        {"text": K("c.price_sma50", p=f"${p:.2f}", op=">" if p > s50 else "≤", s=f"${s50:.2f}"), "ok": p > s50},
        {"text": K("c.sma20_sma50", a=f"${s20:.2f}", op=">" if s20 > s50 else "≤", b=f"${s50:.2f}"), "ok": s20 > s50},
        {"text": K("c.mom20", v=f"{mom20:+.1%}"), "ok": mom20 > 0},
        {"text": K("c.rsi", v=f"{r:.0f}"), "ok": r < 75},
        {"text": K("c.spy200", p=f"${spy[-1]:.2f}", op=">" if spy[-1] > spy200 else "≤", m=f"${spy200:.2f}"), "ok": spy[-1] > spy200},
        {"text": K("c.rank", rank=rank, score=f"{score:+.1%}", slots=slots), "ok": True},
    ]

def exit_check(pos, c):
    p = c[-1]
    pos["peak"] = max(pos["peak"], p)
    hard, trail, s50 = pos["avg_cost"] * (1 - A_HARD_STOP), pos["peak"] * (1 - A_TRAIL), sma(c, 50)
    rules = [("r.hard_stop", p <= hard, K("c.hard", p=f"${p:.2f}", op="≤" if p <= hard else ">", lvl=f"${hard:.2f}", cost=f"${pos['avg_cost']:.2f}")),
             ("r.trail_stop", p <= trail, K("c.trail", p=f"${p:.2f}", op="≤" if p <= trail else ">", lvl=f"${trail:.2f}", peak=f"${pos['peak']:.2f}")),
             ("r.below_sma50", p < s50, K("c.sma50_exit", p=f"${p:.2f}", op="<" if p < s50 else "≥", s=f"${s50:.2f}"))]
    return next((K(n) for n, hit, _ in rules if hit), None), [{"text": txt, "ok": h} for _, h, txt in rules]


# ---------- accounts / engine ----------
def new_account(aid):
    c = ACCOUNTS[aid]
    return {"name": c["name"], "kind": c["kind"], "style": c["style"], "freq": c["freq"], "desc": c["desc"], "rules": c["rules"],
            "cash": INITIAL_CASH, "unsettled": [], "positions": {}, "pending": [], "trades": [], "equity": [],
            "events": [], "last_decision": None, "fees_paid": 0.0,
            "ai": {"done": [], "attempts": {}, "rounds": [], "last_error": None}, **({"ver": RANDOM_VER} if aid == "random" else {}),
            **({"instant": True} if aid == "ai_aggr" else {})}

def migrate(state):
    """In-place upgrades that do not need a full restart. Random control v2: replaces the (idle, all-cash) v1 account."""
    e, d = state["accounts"].get("ai_aggr"), state["accounts"].get("ai_short")
    if d is not None and d["equity"] and (e is None or (not e["trades"] and e.get("cloned_from") != "ai_short")):
        # Account E starts as a perfect copy of D's whole history (trades, equity curve, cash, positions) and diverges from the next run on
        c = ACCOUNTS["ai_aggr"]
        clone = copy.deepcopy(d)
        clone.update(name=c["name"], kind=c["kind"], style=c["style"], freq=c["freq"], desc=c["desc"], rules=c["rules"], instant=True, cloned_from="ai_short")
        state["accounts"]["ai_aggr"] = clone
    elif e is None:                                             # nothing to clone yet
        state["accounts"]["ai_aggr"] = new_account("ai_aggr")
    r = state["accounts"].get("random")
    if r is not None and r.get("ver") != RANDOM_VER and not r["trades"]:
        state["accounts"]["random"] = new_account("random")

def new_state():
    return {"version": STATE_VERSION, "initial_cash": INITIAL_CASH, "universe": UNIVERSE,
            "accounts": {k: new_account(k) for k in ACCOUNTS}, "prices": {}, "events_seen": {}, "updated": None,
            "demo": False, "health": {"last_ok": None, "last_attempt": None, "last_error": None, "fail_count": 0, "ai": {}}}

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

def exec_buy(acct, o, price, ts, spy_px=None):
    sym, px = o["symbol"], price * (1 + SLIPPAGE)
    shares = math.floor(min(o["dollars"], acct["cash"]) / px * 10000) / 10000
    if shares <= 0 or sym in acct["positions"]:
        return
    before = acct["cash"]
    acct["cash"] -= shares * px
    acct["positions"][sym] = {"shares": shares, "avg_cost": px, "peak": px, "entry": ts, "spy_px": spy_px}
    acct["trades"].append({"id": len(acct["trades"]), "time": ts, "side": "BUY", "symbol": sym, "shares": shares,
                           "price": round(px, 2), "fee": 0.0, "pnl": None, "reason": o["reason"],
                           "detail": {**o["detail"], "amount": round(shares * px, 2), "cash_before": round(before, 2),
                                      "cash_after": round(acct["cash"], 2), "spy_px": spy_px, "decided_at": o["decided_at"],
                                      "decision_price": o["decision_price"]}})

def exec_sell(acct, o, price, ts):
    sym = o["symbol"]
    pos = acct["positions"].pop(sym)
    px = price * (1 - SLIPPAGE)
    gross = pos["shares"] * px
    fee = sell_fees(pos["shares"], gross)
    proceeds = gross - fee
    acct["fees_paid"] = round(acct.get("fees_paid", 0) + fee, 2)
    if acct.get("instant"):                                     # experimental account E: sale proceeds are reusable at once
        acct["cash"] += proceeds
    else:
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
    for o in sorted(acct["pending"], key=lambda o: o["side"] != "SELL"):      # sells first so instant-settlement cash can fund the buys
        sym = o["symbol"]
        if sym not in fresh:
            keep.append(o); continue
        if o["side"] == "BUY":
            exec_buy(acct, o, prices[sym], ts, prices.get(BENCH))
        elif sym in acct["positions"]:
            exec_sell(acct, o, prices[sym], ts)
    acct["pending"] = keep

def committed(acct):
    return sum(o["dollars"] for o in acct["pending"] if o["side"] == "BUY")

def traded_today(acct, today):
    return {t["symbol"] for t in acct["trades"] if et_date(t["time"]) == today} | {o["symbol"] for o in acct["pending"]}


# ---------- decision functions ----------
def decide_rule_long(acct, env):
    closes, ts, prices = env["closes"], env["ts"], env["prices"]
    today = et_date(ts)
    pend = {o["symbol"] for o in acct["pending"]}
    for sym, pos in list(acct["positions"].items()):
        if sym not in closes:
            continue
        r, checks = exit_check(pos, closes[sym])
        if r and sym not in pend and et_date(pos["entry"]) != today:
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
        if n >= A_MAX_POS:
            break
        eq = equity_of(acct, prices)
        w, sigma = vol_weight(closes[sym], A_VOL_TARGET, A_W_MIN, A_W_MAX)
        dollars = min(eq * w, acct["cash"] - committed(acct))
        if dollars < 50:
            break
        queue(acct, "BUY", sym, ts, closes[sym][-1], K("r.uptrend", m=f"{score:+.1%}"),
              {"checks": entry_checks(closes[sym], closes[BENCH], score, rank, A_MAX_POS - n) +
                         [{"text": K("c.size", s=f"{sigma:.1%}", t=f"{A_VOL_TARGET:.1%}", w=f"{w:.0%}", lo=f"{A_W_MIN:.0%}", hi=f"{A_W_MAX:.0%}"), "ok": True}], "ind": snap(closes[sym]),
               "equity": round(eq, 2), "weight": round(dollars / eq, 3)}, dollars=dollars)
        n += 1

def decide_rule_short(acct, env):
    closes, ctx, ts, prices = env["closes"], env["ctx"], env["ts"], env["prices"]
    today, hm = et_date(ts), et_hm(ts)
    pend = {o["symbol"] for o in acct["pending"]}
    for sym, pos in list(acct["positions"].items()):
        if sym not in closes or sym in pend:
            continue
        p = closes[sym][-1]
        pos["peak"] = max(pos["peak"], p)
        gain = p / pos["avg_cost"] - 1
        reason = (K("r.tp") if gain >= B_TP else K("r.sl") if gain <= -B_SL else
                  K("r.eod_cut") if hm >= EOD_AT and gain < B_CARRY_MIN else None)
        if reason:
            checks = [{"text": K("c.gain", cost=f"${pos['avg_cost']:.2f}", g=f"{gain:+.2%}", tp=f"{B_TP:.1%}", sl=f"{B_SL:.1%}"), "ok": abs(gain) >= min(B_TP, B_SL)},
                      {"text": K("c.eod", hm=f"{hm[0]:02d}:{hm[1]:02d}", eod=f"{EOD_AT[0]}:{EOD_AT[1]}", g=f"{gain:+.2%}", thr=f"{B_CARRY_MIN:.1%}"), "ok": hm >= EOD_AT and gain < B_CARRY_MIN}]
            queue(acct, "SELL", sym, ts, p, reason, {"checks": checks, "ind": snap(closes[sym])})
    if not (INTRADAY_WINDOW[0] <= hm < INTRADAY_WINDOW[1]) or BENCH not in closes or BENCH not in ctx:
        return
    spy_chg = closes[BENCH][-1] / ctx[BENCH]["open"] - 1
    if spy_chg <= -0.015:
        return
    skip = set(acct["positions"]) | traded_today(acct, today)
    cands = []
    for s in UNIVERSE:
        if s in closes and s in ctx and s not in skip and uptrend(closes[s]):
            dip = closes[s][-1] / ctx[s]["open"] - 1
            if dip <= -B_DIP:
                cands.append((closes[s][-1] / closes[s][-61] - 1 if len(closes[s]) > 60 else 0, dip, s))
    n = len(acct["positions"]) + sum(1 for o in acct["pending"] if o["side"] == "BUY")
    for mom, dip, sym in sorted(cands, reverse=True):
        if n >= B_MAX_POS:
            break
        eq = equity_of(acct, prices)
        c = closes[sym]
        w, sigma = vol_weight(c, B_VOL_TARGET, B_W_MIN, B_W_MAX)
        dollars = min(eq * w, acct["cash"] - committed(acct))
        if dollars < 50:
            break
        checks = [{"text": K("c.dip", open=f"${ctx[sym]['open']:.2f}", d=f"{dip:+.2%}", thr=f"{B_DIP:.1%}"), "ok": True},
                  {"text": K("c.uptrend", s50=f"${sma(c, 50):.2f}", s20=f"${sma(c, 20):.2f}"), "ok": True},
                  {"text": K("c.spy_day", chg=f"{spy_chg:+.2%}"), "ok": True},
                  {"text": K("c.window", hm=f"{hm[0]:02d}:{hm[1]:02d}"), "ok": True},
                  {"text": K("c.size", s=f"{sigma:.1%}", t=f"{B_VOL_TARGET:.1%}", w=f"{w:.0%}", lo=f"{B_W_MIN:.0%}", hi=f"{B_W_MAX:.0%}"), "ok": True}]
        queue(acct, "BUY", sym, ts, c[-1], K("r.dip", d=f"{dip:+.1%}"),
              {"checks": checks, "ind": snap(c), "equity": round(eq, 2), "weight": round(dollars / eq, 3)}, dollars=dollars)
        n += 1

def _hold_forever(acct, closes, ts, syms):
    if acct["trades"] or acct["positions"] or acct["pending"] or not all(s in closes for s in syms):
        return
    each = acct["cash"] / len(syms)
    for s in syms:
        queue(acct, "BUY", s, ts, closes[s][-1], K("r.bh"),
              {"checks": [{"text": K("c.bh", n=len(syms)), "ok": True}], "ind": snap(closes[s]),
               "equity": INITIAL_CASH, "weight": round(1 / len(syms), 3)}, dollars=each)

def decide_spy(acct, env):
    _hold_forever(acct, env["closes"], env["ts"], [BENCH])

def decide_equal(acct, env):
    _hold_forever(acct, env["closes"], env["ts"], UNIVERSE)

def decide_random(acct, env):
    closes, ts, prices = env["closes"], env["ts"], env["prices"]
    today = et_date(ts)
    if acct["last_decision"] == today:
        return
    acct["last_decision"] = today
    rnd = random.Random(f"seed43-{today}")
    pend = {o["symbol"] for o in acct["pending"]}
    for sym in sorted(acct["positions"]):
        x = rnd.random()
        if x < RANDOM_SELL_PROB and sym in closes and sym not in pend and et_date(acct["positions"][sym]["entry"]) != today:
            queue(acct, "SELL", sym, ts, closes[sym][-1], K("r.rnd_sell"),
                  {"checks": [{"text": K("c.rnd_sell", x=f"{x:.2f}", p=RANDOM_SELL_PROB), "ok": True}], "ind": snap(closes[sym])})
    held = set(acct["positions"]) | {o["symbol"] for o in acct["pending"] if o["side"] == "BUY"}
    pool = sorted(s for s in UNIVERSE if s in closes and s not in held and s not in pend)
    for _ in range(max(A_MAX_POS - len(held), 0)):
        x = rnd.random()
        if x < RANDOM_BUY_PROB and pool:
            sym = rnd.choice(pool)
            pool.remove(sym)
            eq = equity_of(acct, prices)
            dollars = min(eq * rnd.uniform(0.10, 0.30), acct["cash"] - committed(acct))      # random size too: 10%-30% of equity
            if dollars >= 50:
                queue(acct, "BUY", sym, ts, closes[sym][-1], K("r.rnd_buy"),
                      {"checks": [{"text": K("c.rnd_buy", x=f"{x:.2f}", p=RANDOM_BUY_PROB), "ok": True}],
                       "ind": snap(closes[sym]), "equity": round(eq, 2), "weight": round(dollars / eq, 3)}, dollars=dollars)


# ---------- AI traders ----------
def market_rows(closes, ctx, intraday=None):
    rows = []
    for s in UNIVERSE:
        c = closes.get(s)
        if not c or len(c) < 61:
            continue
        i, day = snap(c), ctx.get(s, {})
        o, hi, lo = day.get("open"), day.get("high"), day.get("low")
        rets = [c[k] / c[k - 1] - 1 for k in range(len(c) - 20, len(c))]
        mu = sum(rets) / len(rets)
        path = (intraday or {}).get(s, [])
        rows.append({"symbol": s, "price": c[-1], "vs_open": (c[-1] / o - 1) if o else None,
                     "range_pos": ((c[-1] - lo) / (hi - lo)) if (hi and lo and hi > lo) else None,
                     "h1": (c[-1] / path[-5] - 1) if len(path) >= 5 else None,
                     "d1": c[-1] / c[-2] - 1, "d5": c[-1] / c[-6] - 1, "off_high": c[-1] / max(c[-60:]) - 1,
                     "vol20": (sum((x - mu) ** 2 for x in rets) / len(rets)) ** 0.5,
                     "vs_sma20": c[-1] / i["sma20"] - 1, "vs_sma50": c[-1] / i["sma50"] - 1,
                     "rsi": i["rsi"], "mom20": i["mom20"], "mom60": i["mom60"]})
    return rows

def log_decision(rec):
    if DEMO:
        return
    d = os.path.join(os.path.dirname(STATE_PATH), "decisions")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, rec["time"][:10] + ".jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def decide_ai(acct, env, p):
    closes, ctx, ts, prices = env["closes"], env["ctx"], env["ts"], env["prices"]
    today, hm = et_date(ts), et_hm(ts)
    pos_map = acct["positions"]
    pend = {o["symbol"] for o in acct["pending"]}
    # 1) risk exits enforced by code, independent of the AI
    for sym, pos in list(pos_map.items()):
        if sym not in closes or sym in pend:
            continue
        px = closes[sym][-1]
        pos["peak"] = max(pos["peak"], px)
        reason = None
        if p["stop"] and px <= pos["avg_cost"] * (1 - p["stop"]):
            reason = K("r.code_stop", p=f"{p['stop']:.0%}")
        elif p["flatten"] and hm >= p["flatten"]:
            reason = K("r.code_flatten")
        if reason:
            queue(acct, "SELL", sym, ts, px, reason,
                  {"checks": [{"text": K("c.code_exit", px=f"${px:.2f}", cost=f"${pos['avg_cost']:.2f}", chg=f"{px / pos['avg_cost'] - 1:+.2%}"), "ok": True}],
                   "ind": snap(closes[sym])})
    # 2) the AI review round
    st = acct["ai"]
    st["done"] = [k for k in st["done"] if k.startswith(today)]
    st["attempts"] = {k: v for k, v in st["attempts"].items() if k.startswith(today)}
    due = [r for r in p["rounds"] if hm >= r]
    if not due or env["ai"] is None or (p["flatten"] and hm >= p["flatten"]):
        return
    rt = max(due)
    key = f"{today}@{rt[0]:02d}:{rt[1]:02d}"
    if key in st["done"] or st["attempts"].get(key, 0) >= 2:
        return
    if p.get("budget"):                                          # E only uses what is left of the free AI quota
        if not env["budget_ok"]():
            st["paused"] = f"{ts[:16]} daily AI budget reached"
            return
        st["paused"] = None
    if hasattr(env["ai"], "time_left") and env["ai"].time_left() < 40:     # this run has used up its AI time budget: try again next run (no attempt used)
        return
    news = env["news"](p["news_age"])
    pend = {o["symbol"] for o in acct["pending"]}
    eq = equity_of(acct, prices)
    positions = [{"symbol": s, "shares": round(x["shares"], 4), "avg_cost": x["avg_cost"], "price": prices.get(s, x["avg_cost"]),
                  "pnl_pct": prices.get(s, x["avg_cost"]) / x["avg_cost"] - 1,
                  "held_days": (datetime.fromisoformat(today) - datetime.fromisoformat(et_date(x["entry"]))).days}
                 for s, x in pos_map.items()]
    rules = ["long-only, fractional shares", f"min {p['min_hold']} day hold before selling" if p["min_hold"] else "same-day exits allowed"]
    if p["max_buys"] < 50:
        rules.append(f"max {p['max_buys']} new buys per review")
    if p["stop"]:
        rules.append(f"code force-sells at -{p['stop']:.0%} loss")
    if p["max_pos"] >= 50:
        rules.append("no fixed limit on the number of positions or on how many names you buy")
    if p["horizon"] in ("short", "aggr"):
        rules.append("positions MAY be held overnight (gap risk); the 15:30 ET review is the last chance to trade today; no new buys after 15:45 ET")
    if acct.get("instant"):
        rules.append("sale proceeds are available immediately for new buys, so you can rotate within a review")
    payload = {"horizon": p["horizon"], "now_et": f"{today} {hm[0]:02d}:{hm[1]:02d}", "equity": eq,
               "cash": max(acct["cash"] - committed(acct), 0), "max_positions": p["max_pos"], "weight_cap": p["cap"],
               "rules": rules, "positions": positions, "market": market_rows(closes, ctx, env["intraday"]), "news": news}
    messages = aimod.build_messages(payload)
    st["attempts"][key] = st["attempts"].get(key, 0) + 1
    h = env["health_ai"]
    try:
        raw, meta = env["ai"].decide(messages, payload)
        data = aimod.extract_json(raw)
    except Exception as e:
        st["last_error"] = f"{ts[:16]} {str(e)[:200]}"
        h["last_error"] = st["last_error"]
        log_decision({"time": ts, "account": acct["name"], "round": key, "error": str(e)[:500], "prompt": messages[1]["content"]})
        return
    h["last_ok"], h["last_error"], st["last_error"] = ts, None, None
    nid = {n["id"]: n for n in news}
    avail = max(acct["cash"] - committed(acct), 0)
    if acct.get("instant"):                                      # proceeds of sells already queued (by code) will be usable by the buys
        avail += sum(x["shares"] * prices.get(s, x["avg_cost"]) * (1 - SLIPPAGE) for s, x in pos_map.items() if s in pend)
    n_pos = len(pos_map) + sum(1 for o in acct["pending"] if o["side"] == "BUY")
    buys, summary, round_decs, norders = 0, ml(data.get("summary", ""), 160), [], 0
    decisions = sorted((data.get("decisions") or [])[:30], key=lambda d: str(d.get("action", "")).upper() != "SELL")   # sells first
    for d in decisions:
        sym, act = str(d.get("symbol", "")).upper(), str(d.get("action", "")).upper()
        reason = ml(d.get("reason", ""), 200)
        bull = ml(d.get("bull"), 160) if d.get("bull") else None
        bear = ml(d.get("bear"), 160) if d.get("bear") else None
        e = {"symbol": sym, "action": act, "reason": reason, "bull": bull, "bear": bear, "applied": False, "note": None}
        round_decs.append(e)
        if sym not in UNIVERSE or sym not in closes or act not in ("BUY", "SELL", "HOLD"):
            e["note"] = K("n.invalid"); continue
        if act == "HOLD":
            continue
        try:
            conf = max(0.0, min(1.0, float(d.get("confidence", 0.5))))
        except Exception:
            conf = 0.5
        cited = [nid[i] for i in (d.get("headline_ids") or []) if i in nid]
        bad_ids = [i for i in (d.get("headline_ids") or []) if i not in nid]
        ai_info = {"provider": meta["provider"], "model": meta["model"], "confidence": conf, "summary": summary, "round": key,
                   "unverified_ids": bad_ids, "bull": bull, "bear": bear}
        news_info = [{"id": n["id"], "sym": n["sym"], "title": n["title"], "src": n["src"], "age_h": round(n["age_h"], 1)} for n in cited]
        px = closes[sym][-1]
        if act == "BUY":
            if sym in pos_map or sym in pend:
                e["note"] = K("n.held")
            elif n_pos >= p["max_pos"]:
                e["note"] = K("n.full", n=p["max_pos"])
            elif buys >= p["max_buys"]:
                e["note"] = K("n.maxbuys")
            elif p["no_new_after"] and hm >= p["no_new_after"]:
                e["note"] = K("n.late")
            elif sym in traded_today(acct, today) and not p.get("reentry"):
                e["note"] = K("n.traded")
            else:
                try:
                    w = min(max(float(d.get("weight", p["cap"])), 0.05), p["cap"])
                except Exception:
                    w = p["cap"]
                dollars = min(eq * w, avail)
                if dollars < 50:
                    e["note"] = K("n.cash")
                else:
                    chk = [{"text": K("c.ai_reason", why=reason, conf=f"{conf:.0%}"), "ok": True},
                           {"text": K("c.code_ok_buy", w=f"{dollars / eq:.0%}", cap=f"{p['cap']:.0%}", n=n_pos + 1, maxn="∞" if p["max_pos"] >= 50 else p["max_pos"], dollars=f"${dollars:.0f}"), "ok": True}]
                    if bad_ids:
                        chk.append({"text": K("c.bad_ids", ids=", ".join(bad_ids)), "ok": False})
                    queue(acct, "BUY", sym, ts, px, K("r.ai_buy", why=reason),
                          {"checks": chk, "ind": snap(closes[sym]), "ai": ai_info, "news": news_info,
                           "equity": round(eq, 2), "weight": round(dollars / eq, 3)}, dollars=dollars)
                    avail -= dollars; n_pos += 1; buys += 1; norders += 1; e["applied"] = True
        else:
            x = pos_map.get(sym)
            held = (datetime.fromisoformat(today) - datetime.fromisoformat(et_date(x["entry"]))).days if x else 0
            if not x or sym in pend:
                e["note"] = K("n.notheld")
            elif held < p["min_hold"]:
                e["note"] = K("n.minhold", d=p["min_hold"])
            else:
                chk = [{"text": K("c.ai_reason", why=reason, conf=f"{conf:.0%}"), "ok": True},
                       {"text": K("c.code_ok_sell", held=held, minhold=p["min_hold"]), "ok": True}]
                if bad_ids:
                    chk.append({"text": K("c.bad_ids", ids=", ".join(bad_ids)), "ok": False})
                queue(acct, "SELL", sym, ts, px, K("r.ai_sell", why=reason),
                      {"checks": chk, "ind": snap(closes[sym]), "ai": ai_info, "news": news_info})
                norders += 1; e["applied"] = True
                if acct.get("instant"):
                    avail += x["shares"] * px * (1 - SLIPPAGE)
        try:
            learn.record_signal(env["state"], acct["kind"], sym, act, conf, e["applied"], px, ts)
        except Exception:
            pass
    st["done"].append(key)
    st["rounds"].append({"time": ts, "round": key, "provider": meta["provider"], "model": meta["model"], "summary": summary,
                         "decisions": round_decs, "orders": norders, "news_count": len(news)})
    st["rounds"] = st["rounds"][-40:]
    if p["horizon"] == "long" and hasattr(env["ai"], "second_opinion"):      # a second analyst (other model) for comparison; never traded on
        try:
            raw2, m2 = env["ai"].second_opinion(messages, meta["model"], payload)
            if raw2:
                d2 = aimod.extract_json(raw2)
                st["rounds"][-1]["second"] = {"model": m2, "summary": ml(d2.get("summary"), 160),
                    "decisions": [{"symbol": str(x.get("symbol", "")).upper(), "action": str(x.get("action", "")).upper(), "reason": ml(x.get("reason"), 160)}
                                  for x in (d2.get("decisions") or [])[:12] if str(x.get("action", "")).upper() in ("BUY", "SELL", "HOLD")]}
        except Exception:
            pass
    log_decision({"time": ts, "account": acct["name"], "round": key, "provider": meta["provider"], "model": meta["model"],
                  "fallback_errors": meta.get("fallback_errors"), "prompt": messages[1]["content"], "raw_response": raw,
                  "decisions": round_decs, "headlines": [{k: n[k] for k in ("id", "sym", "title", "src")} for n in news]})

KIND = {"rule_long": decide_rule_long, "rule_short": decide_rule_short, "spy": decide_spy,
        "equal": decide_equal, "random": decide_random,
        "ai_long": lambda a, e: decide_ai(a, e, AI_PARAMS["ai_long"]),
        "ai_short": lambda a, e: decide_ai(a, e, AI_PARAMS["ai_short"]),
        "ai_aggr": lambda a, e: decide_ai(a, e, AI_PARAMS["ai_aggr"])}


def compact_equity(acct, now_ts, keep_days=7):
    cutoff = (parse_ts(now_ts) - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    eq = acct["equity"]
    if not eq or eq[0]["time"] >= cutoff:
        return
    daily = {}
    for e in eq:
        if e["time"] < cutoff:
            daily[et_date(e["time"])] = e
    acct["equity"] = list(daily.values()) + [e for e in eq if e["time"] >= cutoff]

def market_snapshot(closes, ctx, ts):
    out = {}
    for s, c in closes.items():
        if len(c) < 61:
            continue
        i, o = snap(c), ctx.get(s, {}).get("open")
        out[s] = {"t": ts, "price": round(c[-1], 2), "prev": round(c[-2], 2), "day": round(c[-1] / c[-2] - 1, 4),
                  "open": round(o, 2) if o else None, "sma20": round(i["sma20"], 2), "sma50": round(i["sma50"], 2),
                  "sma200": round(sma(c, 200), 2) if len(c) >= 200 else None, "rsi": round(i["rsi"], 1),
                  "mom20": round(i["mom20"], 4), "mom60": round(i["mom60"], 4), "spark": [round(x, 2) for x in c[-40:]]}
    return out

def step(state, closes, ctx, ts, news_fn=None, ai=None):
    """One run. closes {sym: [daily closes..., live]}, ctx {sym: {open, high, low}} for today's bar."""
    prices = state["prices"]
    for s, c in closes.items():
        prices[s] = round(c[-1], 4)
    state.setdefault("market", {}).update(market_snapshot(closes, ctx, ts))
    state["market_time"] = ts
    cache = {}
    def news(max_age_h):
        if "all" not in cache:
            cache["all"] = news_fn() if news_fn else []
        now = parse_ts(ts).timestamp()
        items = [n for n in cache["all"] if n["sym"] in UNIVERSE and (now - n["ts"]) / 3600 <= max_age_h][:50]
        return [{"id": f"N{i + 1}", "sym": n["sym"], "title": n["title"], "src": n["src"], "age_h": (now - n["ts"]) / 3600}
                for i, n in enumerate(items)]
    today = et_date(ts)
    intra = state.setdefault("intraday", {"date": today, "px": {}})
    if intra.get("date") != today:
        intra["date"], intra["px"] = today, {}
    for s, c in closes.items():
        intra["px"].setdefault(s, []).append(round(c[-1], 4))
        del intra["px"][s][:-30]
    def budget_ok():
        u = state.setdefault("ai_usage", {})
        used = u.get(today, 0) + getattr(ai, "http_calls", 0)
        cap = (u["limit_hint"] - E_RESERVE) if u.get("limit_hint") else E_DEFAULT_CAP
        return used < cap
    env = {"closes": closes, "ctx": ctx, "ts": ts, "prices": prices, "news": news, "ai": ai, "intraday": intra["px"], "state": state, "budget_ok": budget_ok,
           "health_ai": state["health"].setdefault("ai", {})}
    for aid, acct in state["accounts"].items():
        settle(acct, ts)
        fill_pending(acct, prices, set(closes), ts)
        KIND[acct["kind"]](acct, env)
        acct["equity"].append({"time": ts, "equity": round(equity_of(acct, prices), 2)})
        compact_equity(acct, ts)
    state["updated"] = ts


# ---------- corporate actions ----------
def apply_events(state, evmap, now):
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
    q = res["indicators"]["quote"][0]
    closes = [x for x in q["close"] if x is not None]
    meta, ev = res["meta"], res.get("events") or {}
    live, rt, day = meta.get("regularMarketPrice"), meta.get("regularMarketTime"), None
    if live and rt:
        same_day = datetime.fromtimestamp(rt, NY).date() == datetime.fromtimestamp(res["timestamp"][-1], NY).date()
        if same_day:
            closes[-1] = live
            o, hi, lo = q["open"][-1], q["high"][-1], q["low"][-1]
            if o:
                day = {"open": o, "high": max(hi or o, live), "low": min(lo or o, live)}
        else:
            closes.append(live)
    return {"closes": closes, "meta": meta, "day": day,
            "splits": [(int(v["date"]), v["numerator"] / v["denominator"]) for v in (ev.get("splits") or {}).values()],
            "divs": [(int(v["date"]), v["amount"]) for v in (ev.get("dividends") or {}).values()]}

def session_open(meta, now):
    cp = (meta.get("currentTradingPeriod") or {}).get("regular")
    rt = meta.get("regularMarketTime")
    return bool(cp and rt and cp["start"] <= now < cp["end"] and now - rt <= STALE_SECS)

def sane(sym, d, now):
    c, meta = d["closes"], d["meta"]
    if len(c) < 2 or now - (meta.get("regularMarketTime") or 0) > STALE_SECS:
        return f"{sym}: stale quote"
    if abs(c[-1] / c[-2] - 1) > ANOMALY and not any(t > now - 5 * 86400 for t, _ in d["splits"]):
        return f"{sym}: anomalous jump {c[-1] / c[-2] - 1:+.0%}, skipped"
    return None

def record_usage(state, client, ts):
    """Counts HTTP calls made to the AI per day; remembers the day's quota ceiling if the provider reports one."""
    if client is None:
        return
    u, today = state.setdefault("ai_usage", {}), et_date(ts)
    n = getattr(client, "http_calls", 0)
    if n:
        u[today] = u.get(today, 0) + n
    if getattr(client, "daily_quota_hit", False):
        u["limit_hint"] = u.get(today, 0)
    for k in sorted(k for k in u if k != "limit_hint")[:-30]:
        del u[k]

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
    if state.get("demo") or state.get("version") != STATE_VERSION:    # new experiment version => start clean
        state = new_state()
    migrate(state)
    h = state["health"]
    if state.get("updated") and not os.environ.get("RETRY_AI") and "--force" not in sys.argv \
            and now.timestamp() - parse_ts(state["updated"]).timestamp() < 480:
        print("state was updated less than 8 minutes ago (another run just did it); skipping to keep the cadence."); return
    try:
        spy = fetch(BENCH)
        if not session_open(spy["meta"], now.timestamp()) and "--force" not in sys.argv:
            et = now.astimezone(NY)
            if et.weekday() < 5 and (16, 0) <= (et.hour, et.minute) < (20, 0) and state.get("updated") and et_date(state["updated"]) == et.date().isoformat():
                if os.environ.get("RETRY_AI"):
                    state.get("learn", {}).get("errors", {}).clear()
                client = aimod.Client(pinned=h.setdefault("ai", {}).setdefault("pinned", {}))
                learn.post_close(state, client if client.providers else None, ts, lambda: newsmod.fetch_all(UNIVERSE, NAMES), NAMES, UNIVERSE)
                record_usage(state, client, ts)
                save_state(state)
                print("post-close learning pass done.")
            else:
                print("US market closed or quote stale; nothing to do.")
            return
        h["last_attempt"] = ts
        def f(s):
            try:
                return s, (spy if s == BENCH else fetch(s)), None
            except Exception as e:
                return s, None, str(e)
        with ThreadPoolExecutor(6) as ex:
            got = list(ex.map(f, UNIVERSE))
        closes, ctx, evmap, notes = {}, {}, {}, []
        for s, d, err in got:
            if err:
                notes.append(f"{s}: fetch failed ({err[:60]})"); continue
            bad = sane(s, d, now.timestamp()) if "--force" not in sys.argv else None
            if bad:
                notes.append(bad); continue
            closes[s], evmap[s] = d["closes"], (d["splits"], d["divs"])
            if d["day"]:
                ctx[s] = d["day"]
        if BENCH not in closes:
            raise RuntimeError("benchmark quote unusable: " + "; ".join(notes))
        client = aimod.Client(pinned=h.setdefault("ai", {}).setdefault("pinned", {}))
        h["ai"]["configured"] = client.names()
        if not client.providers:
            h["ai"]["last_error"] = "未配置任何 AI 服务（缺少 GEMINI_API_KEY，且 GitHub Models 不可用），C/D 账户暂不交易"
        if os.environ.get("RETRY_AI"):          # manual runs: give each AI round fresh attempts (for debugging / verification)
            for a in state["accounts"].values():
                a["ai"]["attempts"] = {}
        apply_events(state, evmap, now.timestamp())
        step(state, closes, ctx, ts, lambda: newsmod.fetch_all(UNIVERSE, NAMES), client if client.providers else None)
        record_usage(state, client, ts)
        h["ai"]["configured"] = client.names()
        h.update(last_ok=ts, last_error="; ".join(notes) or None, fail_count=0)
        print("ok", {k: a["equity"][-1]["equity"] for k, a in state["accounts"].items()}, notes)
    except Exception as e:
        h["last_attempt"] = ts
        h["fail_count"] += 1
        h["last_error"] = f"{type(e).__name__}: {e}"[:300]
        print("run failed:", h["last_error"])
    save_state(state)


# ---------- offline demo ----------
TEMPLATES = ["{n} announces new AI partnership", "Analyst raises {n} price target", "{n} shares slip on valuation worries",
             "{n} wins major cloud contract", "Report: {n} supply constraints ease", "{n} faces new regulatory scrutiny"]

def run_demo(days=40):
    global DEMO
    DEMO = True
    rnd = random.Random(7)
    params = {s: (rnd.uniform(0.0002, 0.0012), rnd.uniform(0.01, 0.025), 100 * rnd.uniform(0.5, 3)) for s in UNIVERSE}
    hist = {}
    for s in UNIVERSE:
        drift, vol, p = params[s]
        hist[s] = []
        for _ in range(300):
            p *= math.exp(drift + vol * rnd.gauss(0, 1)); hist[s].append(p)
    dates, d = [], datetime(2026, 10, 30)
    while len(dates) < days:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    state = new_state(); state["demo"] = True
    client = aimod.MockClient()
    N = 26
    for day in dates:
        paths = {}
        for s in UNIVERSE:
            drift, vol, _ = params[s]
            p0 = hist[s][-1]
            p1 = p0 * math.exp(drift + vol * rnd.gauss(0, 1))
            noise = [0.0]
            for _ in range(N):
                noise.append(noise[-1] + rnd.gauss(0, vol / math.sqrt(N)))
            paths[s] = [p0 * math.exp(math.log(p1 / p0) * i / N + noise[i] - noise[N] * i / N) for i in range(N + 1)]
        for k in range(1, N):
            t = (datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY) + timedelta(minutes=15 * k)).astimezone(timezone.utc)
            ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
            closes = {s: hist[s] + [paths[s][k]] for s in UNIVERSE}
            ctx = {s: {"open": paths[s][0], "high": max(paths[s][:k + 1]), "low": min(paths[s][:k + 1])} for s in UNIVERSE}
            def fake_news(t=t):
                r = random.Random(ts)
                return [{"sym": s, "title": r.choice(TEMPLATES).format(n=NAMES[s]), "src": "Demo Wire",
                         "ts": t.timestamp() - r.uniform(0, 6) * 3600} for s in UNIVERSE if r.random() < 0.5]
            step(state, closes, ctx, ts, fake_news, client)
        close_t = datetime(day.year, day.month, day.day, 16, 5, tzinfo=NY).astimezone(timezone.utc)
        def close_news(t=close_t):
            r = random.Random(t.isoformat())
            return [{"sym": s, "title": r.choice(TEMPLATES).format(n=NAMES[s]) + f" ({r.randint(1, 999)})", "src": "Demo Wire", "ts": t.timestamp() - r.uniform(0, 20) * 3600}
                    for s in UNIVERSE if r.random() < 0.6]
        learn.post_close(state, client, close_t.strftime("%Y-%m-%dT%H:%M:%SZ"), close_news, NAMES, UNIVERSE)
        for s in UNIVERSE:
            hist[s].append(paths[s][N])
    state["health"].update(last_ok=state["updated"], last_attempt=state["updated"])
    save_state(state)
    for k, a in state["accounts"].items():
        print(f"demo {k}: equity={a['equity'][-1]['equity']} trades={len(a['trades'])} fees={a['fees_paid']}")

if __name__ == "__main__":
    run_demo() if "--demo" in sys.argv else run_live()
