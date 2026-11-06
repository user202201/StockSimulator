# 实验登记（Pre-registration）

> 开跑后只增不改。要改规则 = 新建一个带版本号的账户，旧账户保持冻结。

**正式开始日期**：待定（空跑稳定后填写）　**初始资金**：每个账户 $5,000　**市场**：美股，仅做多，不加杠杆，允许碎股
**账户模型**：现金账户（买入只能用已交收资金，不触发 PDT 规则；若实际使用保证金账户，日内交易将受“5 个交易日内最多 3 次”限制）

## 股票池（固定，21 只）
- 核心：SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AVGO
- AI 相关：AMD, PLTR, TSM, ORCL, ARM, MU, MRVL, SMCI, SNOW, NOW
- PTC（PTC Inc）

注：均为事后的强势大盘股，存在幸存者偏差，必须与 equal 账户对比解读。

## 账户（2×2 设计：规则 vs AI × 中线 vs 日内，另加三个对照）
| 账户 | 决策者 | 频率 | 说明 |
|---|---|---|---|
| A 趋势规则 `rule_long` | 预设规则 | 每 15 分钟检查 | 趋势跟随，持有数天到数周 |
| B 日内规则 `rule_short`（v2） | 预设规则 | 每 15 分钟检查 | 上升趋势中盘中回调 ≥1.2% 低吸，+1.0% 止盈 / -0.8% 止损；15:30 收盘前检查：没有盈利的卖出，有盈利的留仓过夜。**不再强制清仓（v1 曾每天 15:30 清仓）** |
| C AI 稳健 `ai_long`（v2） | AI + 新闻 | 每天 1 次（美东 10:00） | 单只 ≤25%，买入后至少持有 2 天，-10% 强制止损；**不限制持仓只数和选股**，按操盘手惯例自行保留一定现金 |
| D AI 短线 `ai_short`（v3） | AI + 新闻 | 每天 5 次（10:00/11:30/13:00/14:30 + 15:30 收盘复盘） | 单只 ≤20%，-3% 强制止损，15:45 后不开新仓；可隔夜持有，15:30 由 AI 决定每个仓位留还是卖，也可在收盘前追加买入；**不限制持仓只数和选股、可当日重复买入同一只**，按惯例自行保留一定现金 |
| **E AI 激进 `ai_aggr`**（2026-11-04 起独立，此前是 D 的副本） | AI + 新闻 | 每 30 分钟（10:00–15:30，约 12 次，受免费 AI 额度限制） | 从 D 克隆、目标是**提高资金周转率**：无持仓数量限制、无强制止损、无每轮买入限制，只保留单只 ≤50%、只做多、不加杠杆；**实验设定：卖出资金即时可再用、不受 PDT 限制**（与其他账户不同，对比时需注意）；额度保护：当天 AI 调用总数达到预算（默认 60，或观察到的服务商日上限减 8）后自动暂停 E |
| spy | 无 | 开局一次 | 基准：SPY 买入持有 |
| equal | 无 | 开局一次 | 基准：21 只等权买入持有（抵消幸存者偏差） |
| random | 随机 | 每天抽签 | 对照（v2，2026-11-03 起）：每个持仓每天 10% 概率卖出，每个空位每天 50% 概率随机买入一只，最多 4 只——持仓水平和交易频率与交易员相近，才能公平检验“是否优于运气”。v1 因抽签长期未命中而全程空仓，没有产生任何交易，已被替换 |

对比逻辑：A vs C = 同为中线，AI 是否优于规则；B vs D = 同为日内，AI 是否优于规则；A vs B、C vs D = 交易频率的影响；全部 vs random / equal / spy = 是否优于运气与买入持有。

## AI 规则
- AI 只看到：行情指标（价格、与均线关系、RSI、动量、相对今日开盘涨跌）、最近的新闻标题（Yahoo Finance RSS，不足时用 Google News RSS）、自己的持仓。
- AI 输出固定格式 JSON；**所有风控由代码强制**：标的白名单、仓位上限、最大持仓数、每轮买入次数上限、最短持有期、强制止损、日内账户强制清仓。AI 引用的新闻编号由代码核对，不存在的编号会被剔除并标注。
- 温度 0；每次决策的完整提示词、原始回答、模型名、新闻列表写入 `data` 分支 `decisions/日期.jsonl`。
- AI 服务失败 → 该账户本轮不交易（按“持有”处理），不换用未标注的模型；使用了备用服务商会在记录中标明。
- **提示词 v2（2026-11-03，正式开始日期之前）**：C、D 的资料和角色刻意拉开差异，避免两位 AI 看到同样的输入、做出同样的决策：
  - C「耐心波段手」：只看日线视角（今日/5日/20日/60日涨跌、相对 50 日均线、距 60 日高点、波动率），新闻窗口 36 小时；被要求不追当日已涨超 3% 的股票、偏好回调买入和 2–4 只集中持仓、持有 3–15 个交易日。
  - D「日内交易员」：看日内视角（相对开盘、在当日区间中的位置、最近约 60 分钟涨跌、相对 20 日均线、波动率），新闻窗口 8 小时；目标 +1%~+2%、快速止损、关注未被充分消化的新消息，可以轮换持仓。
  - 此后提示词冻结；修改即视为新版本账户。

## 成交与费用（引擎强制，2026 年美国零售券商标准）
- 决策价与成交价分离：订单在**下一次行情**时成交，每边 0.05% 滑点
- 佣金 $0；卖出时收 SEC Section 31 费（$20.60 / 百万美元，2026-04-04 起）与 FINRA TAF（$0.000195 / 股，单笔 $0.01–$9.79），**每年 10 月和 4 月复核**
- 卖出资金 T+1 交收；拆股、分红自动处理；休市/提前收盘/行情过期/价格异常时不交易
- 不模拟：资本利得税、汇率损耗、股息预扣税

## 评价指标
总收益、相对 SPY / equal / random 的超额、最大回撤、夏普比率、日均成交数与换手率、已平仓胜率与平均盈亏、分行情阶段表现。结论需给出置信区间；半年数据只能说“有迹象”，不能说“证明有效”。

## 不做的事
不事后调参；不因短期亏损临时改规则；不把模拟收益当作实盘预期。

## 学习功能（不参与任何交易决策）
- **提示词 v3**：AI 每笔买入前必须先写“看多理由”和“看空理由”，看空不弱于看多就不买；两种理由都会记录并显示。
- **第二位分析师**：C 每天的复盘会让另一个 Gemini 型号（和主模型不同）看同样的资料给出意见，仅作对比，不用于交易。
- **信号日志与信心校准**：AI 每一次买卖建议（不论是否被代码放行）都记录信心和价格，次日收盘后统计正确率。
- **收盘后学习任务**（每交易日最多 3 次调用）：新闻类型/情绪标注（用于“什么新闻真的影响股价”研究）、当日已平仓交易的点评、当日日志与一课。
- **你 vs AI 预测挑战**：预测保存在浏览器本地，与 AI 建议的正确率比较。
- 所有 AI 生成内容标注“可能有误，仅供学习”。学习任务失败时只会跳过，不影响交易。

## 版本记录
- 2026-11-03：B、D 取消每日强制清仓（v2）。原因：强制清仓是实现者额外加上的限制，会让短线账户每天多付一次来回成本、无法根据收盘前新闻追加买入，并使数据过于碎片化。正式开始日期之前调整。
- 2026-11-03：C、D 取消持仓只数和每轮买入次数限制，保留单只比例上限与基本止损；提示词加入“按操盘手惯例保留一定现金”。
- 2026-11-04：新增账户 E（从 D 克隆的激进高周转版本）。E 的起点是 **D 到 2026-11-03 收盘为止的完整历史副本**（交易、净值曲线、现金、持仓都相同），从 2026-11-04 的第一轮运行起才各自独立，因此 E 和 D 的曲线在此之前完全重合，之后的差异只来自两者规则不同。比较时请同时看收益、回撤、换手率和费用。
- 2026-11-04：C 的提示词加入资金使用方针（软性）：波段投资者通常 70%–90% 仓位，现金超过 30% 需在总结里说明理由。原因：C 首次决策时只投了 40%，现金过多使其收益主要由“没投的钱”决定，削弱了与基准的可比性。代码层面不强制。
- 2026-11-04：**仓位不再等额、也不再有统一的单只上限**。A、B 的仓位按波动率调整（A 每只 10%–30%，B 8%–25%，波动越大买得越少）；随机账户每次买入 10%–30% 随机；C、D、E 取消单只仓位上限（可以把大部分资金压在一只股票上），提示词要求按把握和风险定仓位，不要平均分配。限制只剩下可用资金、只做多、不加杠杆（以及各自的止损等基本风控）。SPY、等权两个基准按定义保持不变。
- 2026-11-04：AI 调用增加自动降级：主模型出问题时依次尝试其他 Flash 型号，最后才用 Flash-Lite；Gemini 全部失败时才用可选的备用服务商。每条决策都记录实际回答的模型。降级只在出错时发生，不改变主模型的固定选择。
- 2026-11-04（太保守的修正）：开盘首日是强势上涨，C、D 因“不追涨、等回调”整日观望，B 因只做回调低吸而空仓，使模拟几乎没有数据。调整：**C** 不再要求等回调，仓位低于 70% 且有合适机会时应分批建仓；**D** 增加顺势跟进强势股，并要求每轮有空闲现金时通常至少开一个新仓；**B** 增加第二种入场（较开盘涨 1%–6% 且贴近日内高点的顺势跟进），最多 6 只；**A** 最多 6 只，总仓位上限 95%。

### 2026-11-04 — Per-trader review, step 1: A (trend rules)
Finding: AAPL bought 11-02, sold 11-03 on a bare 50-day-average break (-$32), re-bought 11-04 7.6% higher: whipsaw, not "days to weeks".
Change: the 50-day exit needs a 2% buffer; no re-buy for 3 days after a sale. Next in the review: B, then C (target: heavy invested share, small reserve), D, E.
A, follow-up (same day): trend follower made consistent with its label — entries and exits (except the hard stop) are decided only at the 15:45 ET check (closing-price confirmation, filled at the next open); trailing stop = 4 x daily volatility (7%-15%), hard stop = trail + 2% below cost; RSI<75 limit waived near the 60-day high (breakouts); description now states the expected profile (win rate ~40%, few big winners, weeks of holding).

### 2026-11-04 — Per-trader review, step 2: B (short-term rules)
Owner's direction: B is A at a faster pace (small, steady wins, quick turnover), may stand aside but not for long, no daily flatten.
Change: B trades only trend stocks with the market regime filter (SPY above its 200-day average); take-profit 0.8x and stop 1.0x daily volatility (1%-3% / 1%-3.5%); dip/breakout thresholds scale with volatility; exposure cap 95%; exits also on a 2% break of the 50-day average (confirmed near the close) and after 7 calendar days (rotation). The end-of-day cut (sell everything not in profit) is removed.
B, follow-up: decisions must be independent from A. B ranked candidates by A's 60-day momentum and used A's 50-day trend test, so both bought the same stocks. Now B uses its own short-horizon view: price above a rising 20-day average, candidates ranked by how unusual today's move is (in daily-volatility units), trend exit at 1% below the 20-day average. A stays the slow, long-horizon trend follower.

### 2026-11-04 — Per-trader review, step 3: C (AI swing investor)
Finding: C bought on catalysts/bounces (ORCL, AMD) and chased the post-election spike, had no market-regime input, a 70-90% invested target and no stated purpose for the reserve.
Change (prompt v3, one re-review): MARKET REGIME block (SPY vs 50/200-day, share of stocks in an uptrend) added to every AI prompt; C must state its view of the overall trend first, buys mainly on trend (catalysts only support), target about 90% invested with a ~10% reserve used to add on pullbacks and for emergencies, >20% cash needs a stated reason. No code-level floor yet; revisit after a few days of data.
Dashboard: data-check timestamp shown in the sidebar, poll every 30 s.

### 2026-11-04 — Per-trader review, step 4: D (AI short-term trader)
Finding: D's buys on the post-election spike (AAPL/MSFT) were justified by "mega-cap leader", the same story as C and E; its closing (15:30) review had not run yet in live conditions; the description lacked an expected profile.
Change (prompt v3, one re-review): every D buy must cite an intraday signal (move vs open, position in today's range, last-60-minute change); daily trend alone is C's domain. Description states the profile (+1-3% targets, about -1.5% cut, hours to days, 50-90% invested). To verify: today's 15:30 review gives a carry/sell decision for each position.

### 2026-11-04 — Per-trader review, step 5: E (AI aggressive, high turnover)
Finding: E behaves as designed (fully invested, sells one name and buys another in the same review with instant proceeds) but the page could not measure the point of the experiment, whether higher turnover is more efficient.
Change: comparison table gains trades per day, average holding time in hours, average net P&L per closed trade, trading cost (fees + estimated slippage) and capital efficiency (return / turnover) for every trader; E's prompt (v1, one re-review) requires every buy reason to cite an intraday signal, like D; E's description states its profile (12 reviews a day, +0.5-2% targets, about -1% cut, 85-100% invested). E keeps no code stop on purpose.
Note: on 2026-11-04 a GitHub-hosted runner queued trade #38 for 21 minutes (platform delay), so two 15-minute slots were skipped; AI rounds are slot-based and catch up on the next run.

2026-11-04 (evening): E was paused at 15:45 ET by our own safety budget (60 calls/day, a guess), not by Google: 119 HTTP calls had been counted, most of them failed/retried 503 calls during the overload. Now only answered calls count and the default budget is 200/day; a real "per-day quota" 429 from Google still sets the observed limit and E then stays under it. The sidebar shows today's counted calls.

2026-11-04 (evening): the 55 "429 TooManyRequests" seen in AI Studio were per-minute bursts: C, D and E reviewed at the same minute and each failure was retried 3x. Now: at least 15 s between any two AI requests, one wait-and-retry on 429 then the next model, and review slots are spread: D 10:00/11:30/13:00/14:30/15:30, E on :15/:45 (10:15-15:15, 11 reviews), C at 10:30.
Smart deferral (same evening): if two reviews fall into the same run, only the first one asks the AI; the second waits for the next run (once per review, so nothing starves). Priority: D (time-critical), then E, then C. This sits on top of the offset schedule and the 15 s minimum gap, for the case where delayed runs make slots collide anyway.

### 2026-11-06 — rate-limit handling and chain safety net
Finding: AI Studio showed 55 x 429 on 11-04 and on 11-05 every model answered 429 at 15:45 ET after only 18 successful calls; our error log cut the body at 160 characters, so per-minute and per-day limits were indistinguishable and the code kept retrying. Also tick run #63 was cancelled externally after 64 min of sleeping (no cause in the log); a cron run revived the chain at 12:04 UTC.
Change: the 429 body is now parsed; a per-day quota marks that model dead until midnight Pacific (the ladder skips it, the dashboard lists it); a per-minute limit waits as long as the API asks, once; when every model is dead the reviews wait instead of burning attempts; the latest 429 (kind, model, time) is shown in the sidebar. tick.yml gets extra weekday cron revivals around the market open (12:47, 13:17, 13:47, 14:17 UTC).
