# DEVELOPMENT.md — 开发者与 AI 上下文手册

> 目的：新开会话（人或 AI）读完这一份文件，就能接着开发、排障、运维，不需要重新通读代码。
> 配套文件：`EXPERIMENT.md`（预注册规则 + 按日期的变更日志 + 证据）、`README.md`（上线与使用说明）、`CLAUDE.md`（给 Claude Code 的入口，指向本文件）。
> 本文件写于 2026-11-14，对应的最后一次代码提交见 `git log`。**改了行为就同步改这里和 EXPERIMENT.md。**

---

## 0. 一分钟版

- 这是一个**美股模拟交易实验**，全自动、免费、托管在 GitHub：Actions 里的机器人每 15 分钟跑一次，拉真实行情，按各自策略下“模拟单”，结果写到 `data` 分支的 `state.json`，同时发布到 GitHub Pages 的静态看板（`docs/index.html`，单文件）。
- 9 个账户同台比较：6 个“交易员”（A 到 F）+ 3 个对照组。初始资金各 $5,000，规则、数据、成交与费用模型完全相同。
- 目的不是赚钱，是**比较不同决策方式**（规则 vs AI，长线 vs 短线，对冲 vs 不对冲）在同一市场里的表现，并让用户（中文使用者）通过看板学习。
- 全部是 **Python 标准库**（无第三方依赖）+ **原生 JS 单页**（只内置 Chart.js）。没有构建步骤、没有测试框架（验证方式见 §13）。

## 1. 工作约定（和用户相处的规则）

| 项目 | 约定 |
|---|---|
| 沟通语言 | 用户是中文使用者，**默认用中文回复**；用户偶尔用英文提问时可用英文。代码、提交信息、注释用英文，UI 文案三语（zh/en/fr）。 |
| 隐私 | 用户邮箱仅用于识别身份，**绝不发送到任何外部服务**。仓库公开，不要写入个人信息。 |
| 开发分支 | 唯一的代码分支是 `claude/zealous-faraday-yzun8q`（工作流也在它上面运行，见 §3）。机器人数据在 `data` 分支。**没被要求不要创建 PR。** |
| 提交 | 提交信息英文，结尾带系统要求的署名行；每次改动后 `git push -u origin claude/zealous-faraday-yzun8q`。 |
| 诚实 | 不确定就说不确定；**不要把未证实的传闻当事实喂给 AI 或写进页面**（用户提过“老黄”等说法，只有查到可靠来源的才使用，并标注为“报道”）。解释亏损时不要事后诸葛。 |
| 不干涉选股 | 用户明确要求：AI 交易员（C/D/E/F）**不限制持仓只数、不限制单只仓位、不限制选哪只股票**，只保留基础风控（止损等）。 |
| 独立性 | 各交易员的决策应相互独立，不要让 B 抄 A、D 抄 C（B 曾因共用 60 日动量排序而和 A 买同一批，已改）。 |
| 先证据后改动 | 每次校准都写进 EXPERIMENT.md 并给出数据依据。 |
| 提醒 | 改提示词要 bump `PROMPT_VER`；改规则要同步三语文案；样本期很短（几周），不要因为单日表现大改架构。 |

## 2. 系统总览

```
 ┌──────────── GitHub Actions ────────────┐
 │ tick.yml (计时器)  ──每15分钟/睡眠──▶ trade.yml (干活)
 │      ▲                                   │ python bot/run.py
 │      └──── trade 结束时再派发 tick ◀─────┤   Yahoo 行情 → 引擎 → AI → 写 state.json
 └──────────────────────────────────────────┘          │
                                             commit 到 data 分支
                                                       │
                          build site = docs/ + 最新 state.json  → actions/deploy-pages
                                                       │
                       浏览器：docs/index.html 每 30 秒轮询 data/state.json，变化即重绘
```

数据流（一次 `trade` 运行）：
1. `run_live()` 读 `state.json` → `migrate()` → 8 分钟内刚更新过则跳过。
2. 取 SPY 判断是否开盘（`session_open`）；休市则只在工作日 16:00–20:00 ET 做“盘后学习/精选”。
3. 并行取 UNIVERSE + HEDGE_SYMS 的日线（1 年，含拆股/分红事件），逐个 `sane()` 校验。
4. `apply_events()`（拆股/分红）→ `step()`：对每个账户 `settle` → `fill_pending` → `KIND[kind](acct, env)` → 追加净值。
5. `record_usage()`、保存状态；workflow 把 `state.json` 提交到 `data` 分支、构建站点并部署 Pages、再派发 `tick`。

## 3. 基础设施（`.github/workflows/`）

### 3.1 工作流
| 文件 | 作用 | 要点 |
|---|---|---|
| `trade.yml` | 真正干活，一次约 1–2 分钟 | 并发组 `trade`（不取消排队）；`environment: github-pages`；步骤：确保 `data` 分支存在 → checkout `data` 到 `databranch/` → `python bot/run.py`（`STATE_PATH=databranch/state.json`）→ 提交到 data → 构建站点 → configure/upload/deploy pages（deploy 失败自动等 20 秒重试一次）→ **无论成败**最后派发 `tick.yml`（最多重试 5 次）。cron `*/15 12-21 * * 1-5` 仅作保险。手动运行（非 chained）会设 `RETRY_AI=1`，给每个 AI 复盘重新尝试机会。 |
| `tick.yml` | 计时器 | `python3 bot/next_run.py` 算出 `<秒数> <trade 或 tick>`，睡眠后派发 `trade`（`-f chained=true`）或 `tick`。并发组 `tick`（`cancel-in-progress: false`，同一时间一条链）。`timeout-minutes: 330`。cron：`17 */4 * * *` 加工作日开盘前后 `47 12`、`17,47 13`、`17 14`（UTC，覆盖夏令时/冬令时的 9:30 ET）作为复活途径。 |
| `keepalive.yml` | 每周心跳 | 在 `data` 分支做一次真实提交 + `gh workflow enable trade.yml` + 派发 `tick`，防止 GitHub 因 60 天无活动停用定时任务。 |

### 3.2 `bot/next_run.py`
`decide(now_ET)`：
- 工作日 09:30:45–16:20 ET：睡到下一个整刻钟 +45 秒，类型 `trade`。
- 其余时间（夜里/周末）：朝下一个工作日 09:30:45 ET 睡，每段最多 5 小时（`tick` 类型，保持链不断），最后一段 `trade`。夏令时用“墙钟 + UTC 差”计算。

### 3.3 分支与密钥
- 分支：代码和工作流都在 `claude/zealous-faraday-yzun8q`；工作流用 `--ref "$GITHUB_REF_NAME"` 在同一分支上派发。`data` 分支只放 `state.json`、`decisions/*.jsonl`（实时决策日志，含完整提示词和 AI 原始回答）、`heartbeat.txt`。
- Secrets：`GEMINI_API_KEY`（必需）、可选 `AI_BASE_URL`/`AI_API_KEY`（备用 OpenAI 兼容服务）。Variables：`GEMINI_MODEL`、`AI_MODEL`、`GH_MODEL`、`ENABLE_GH_MODELS`。**GitHub Models 默认关闭**（观察到只返回 "OK"）。
- 仓库必须公开：免费 Pages 与不限时 Actions 的前提；私有仓库每月 2000 分钟会被 tick 链用光。

### 3.4 已观察到的 GitHub 平台行为（排障时先想到这些）
1. **cron 不可靠**（常迟到数小时）：所以主链不靠 cron，靠 trade→tick→trade 接力；cron 只是复活保险。
2. tick 睡眠中偶发被外部取消（2026-11-06 一次，64 分钟，日志只有 “The operation was canceled”）；靠备用 cron 复活。列表里的 `cancelled` tick 多数是排队被替换，属正常。
3. 免费运行机偶发排队（2026-11-04 一次等了 21 分钟，错过两个 15 分钟档）；AI 复盘按“时间槽”记录，晚到会补做。
4. `deploy-pages` 偶发 “Found 0 artifact(s)”（刚上传就找不到）；已加一次重试。数据和链路不受影响。
5. Pages 只在**运行**时部署，单纯推代码不会更新网页；下一次 trade 运行（≤15 分钟）才会带上新页面。浏览器可能需要 Ctrl+Shift+R 一次（页面会检测 ETag 变化自动重载）。
6. 沙箱环境（开发会话）**访问不了 Yahoo、Google News、Pages**（代理 403），所以抓取代码在沙箱里无法实测，只能在真实运行后读 `data` 分支验证。

## 4. 账户一览

| id | 名称 | 类型 | 决策时点（美东） | 关键规则 |
|---|---|---|---|---|
| `rule_long` | A 趋势规则 | 规则，长线趋势跟随 | 每次运行评估，但**入场与除硬止损外的出场只在 ≥15:45 确认** | 买：现价>SMA50、SMA20>SMA50、20 日动量>0、RSI<75 **或**距 60 日高点 2% 内（突破豁免）、SPY>200 日线；按 60 日动量排序，最多 6 只，波动率定仓（10–30%），总仓≤95%。卖：从最高点回撤（4×日波动，7–15%）/ 跌破 SMA50 的 2% / 硬止损（回撤线再 -2%，成本价算，盘中也生效）；卖出后 3 天内不买回；买入当天不卖。 |
| `rule_short` | B 日内规则 | 规则，A 的快节奏版 | 每次运行；入场窗口 10:00–15:30 | 自己的短期视角：现价>SMA20 且 SMA20 高于 5 天前（`short_trend`）；入场=较开盘回落（低吸）或上涨且在日内区间上沿（追强），阈值=0.6×日波动（0.8%–2%）；候选按“今日异动幅度（相对日波动）”排序（不用 60 日动量）。要求 SPY>200 日线、SPY 当日跌幅<1.5%、**VIX 不低于 28 或单日涨 15% 时不开新仓**（`risk_off`）。止盈 0.8×日波动（1–3%）、止损 1.0×日波动（1–3.5%）、收盘前跌破 SMA20 的 1%、持有满 7 天换仓；**不再每天清仓**；最多 6 只，波动率定仓（8–25%），总仓≤95%。 |
| `ai_long` | C AI 稳健 | AI 波段投资者 | 10:30（一天一次） | 趋势买入为主，目标约 90% 仓位，现金>20% 要说理由；每次复盘写“大势判断” `outlook`（看多/中性/谨慎/看空），下次复盘带着它；代码止损 -10%，买入后至少持有 2 天；还会调“第二位分析师”（另一模型，仅供对比，不交易）。 |
| `ai_short` | D AI 短线 | AI 日内短线 | 10:00、11:30、13:00、14:30、15:30 | 买入理由必须引用日内信号；代码止损 -3%；15:45 后不开新仓；可隔夜；看到自己最近 40 笔平仓的胜率/盈亏平衡胜率。 |
| `ai_aggr` | E AI 激进 | D 的激进克隆（2026-11-03 复制 D 的历史，11-04 起分叉） | :15 与 :45，10:15–15:15 共 11 次 | 高周转；**卖出款立即可用**（`instant`），无代码止损；受 AI 额度预算限制（`budget`）。 |
| `ai_hedge` | F AI 对冲 | AI 日内短线 + 对冲工具 | 11:00、12:30、14:00、15:00 | 除 21 只股票外可买 SH、PSQ（-1× 反向 ETF）与 GLD、XLP、XLU；先判断方向，转跌转到反向/防御/现金；代码止损 -3%；优先级最低、额度紧张时最先暂停；**从 2026-11-16 起**（`acct["started"]` 在首次运行时写入），只能与同期比较。 |
| `spy` | SPY 买入持有 | 对照 | 首次运行满仓 SPY | 回答“折腾半天有没有比直接买大盘强”。 |
| `equal` | 21 只等权持有 | 对照 | 首次运行等分买入 21 只 | 抵消“所选股票本来就强”的偏差；**股票池不含对冲标的**。 |
| `random` | 随机决策 | 对照（v2） | 每天 | 每个持仓每天 10% 概率卖，每个空位 50% 概率随机买（10–30% 仓位，最多 4 只），检验是否比运气强。 |

股票池 `UNIVERSE`（21 只）：SPY、QQQ、AAPL、MSFT、NVDA、AMZN、GOOGL、META、TSLA、AVGO、AMD、PLTR、TSM、ORCL、ARM、MU、MRVL、SMCI、SNOW、NOW、PTC。`HEDGE_SYMS`：SH、PSQ、GLD、XLP、XLU（仅 F）。

实验起点：多数账户 2026-11-02；random v2 自 11-03；E 分叉于 11-04；F 自 11-16。**“正式对比起点”建议定为 2026-11-05 之后（之前规则变动多），但用户还没确认**——别擅自当成既定事实。

## 5. 引擎：`bot/run.py`（约 1060 行）

按文件顺序：

1. **常量**：`INITIAL_CASH=5000`、`SLIPPAGE=0.0005`（每边）、SEC/TAF 卖出费用、`STALE_SECS=1800`、`ANOMALY=0.25`；A/B 的参数组（`A_*`、`B_*`）；`PROMPT_VER`（见 §6.6）；`AI_PARAMS`（每个 AI 账户一组）；`ACCOUNTS`（名称/说明/规则的中文原文，创建账户时拷贝进 state，**页面实际显示的三语文案来自 `docs/index.html` 的 `AC`**，两处要同步）。
2. **小工具**：`K(key, **args)` 生成可翻译文本对象 `{"k","a"}`（页面用 `TPL` 渲染）；`ml()` 规整 AI 的 `{zh,en,fr}` 文本；`sma/rsi/snap/vol20/vol_weight`；时区 `et_date/et_hm/parse_ts`。
3. **规则 A 的函数**：`market_regime_ok`、`entry_score`、`near_high`、`entry_checks`、`trail_pct`、`exit_check(pos, c, close_ok)`；`decide_rule_long`。
4. **账户与状态**：`new_account`、`migrate`（就地升级：克隆 D→E、重建 random v2、**补建缺失的 F**、写 `hedge_universe`）、`new_state`、`equity_of`。
5. **成交模型**（所有账户共用，AI 管不到）：
   - `queue()` 只入队；**下一次运行**才 `fill_pending()`（先卖后买），按该次价格成交，买价 ×(1+滑点)，卖价 ×(1−滑点)。
   - 买入只用**已结算现金**（现金账户，无 PDT）；`exec_buy` 用 `min(dollars, cash)`，份额取 4 位小数向下取整（支持碎股）。
   - 卖出扣 SEC/TAF 费；收入进 `unsettled`，`avail = next_trading_day`（T+1），`settle()` 到期入现金；**E 例外**（`instant`）。
   - `apply_events()`：拆股调整份额/成本，分红按持股计入现金；`events_seen` 避免重复。
6. **规则 B**：`short_trend`、`b_levels`、`risk_off`、`decide_rule_short`。
7. **对照组**：`decide_spy`、`decide_equal`（`_hold_forever`）、`decide_random`。
8. **AI 相关辅助**：`track_record`（最近 40 笔平仓统计：胜率、均盈、均亏、盈亏平衡胜率）、`regime_rows`（SPY 相对 50/200 日线、20 日动量、52 周回撤、半年动量、上升趋势股票数、200 日线之上数量，**只统计 UNIVERSE**）、`market_rows`（给 AI 的行情表，F 会额外含对冲标的）、`log_decision`（把完整提示词+回答追加到 `decisions/<日期>.jsonl`，演示模式不写）。
9. **`decide_ai(acct, env, p)`**（核心，约 200 行）按顺序：
   1. 代码强制的风险出场（止损、可选的收盘清仓；`p["stop"]`）。
   2. 提示词版本检查：`st["pv"] < PROMPT_VER[horizon]` ⇒ 清空当天 `done/attempts`，用新提示词**重审一次**。
   3. 到点判断：`due = [r for r in rounds if hm >= r]`，取最近一档 `rt`，`key="日期@HH:MM"`；已 `done` 或当天 `attempts>=2` 则返回。
   4. 门禁：`budget`（E/F：`budget_ok()` 否则 `st["paused"]`）→ `exhausted_all`（所有模型当天额度用完）→ 本次运行 AI 时间预算 `<40s` → **碰撞顺延**（同一次运行里别的账户 5 分钟内刚用过 AI，则本复盘顺延到下一次运行，每个复盘最多顺延一次，`st["deferred"]`）。
   5. 组装 `payload`（持仓、行情表、`regime`、`macro`、新闻、`record`、`prev_outlook`、规则文字）→ `aimod.build_messages` → `env["ai"].decide`。
   6. 逐条决策：校验标的（UNIVERSE 或 `extra`）、动作；BUY 受 `max_pos/max_buys/no_new_after/重复买入/现金≥$50` 约束，权重 `min(max(w,0.05),cap)`；SELL 受 `min_hold` 约束；引用的新闻编号核对（含宏观新闻 M 编号）；记录 `signals`（学习用）。
   7. 写 `st["rounds"]`（保留 40 条）、C 的 `outlook`、C 的第二位分析师、`log_decision`。
10. **`KIND`**：`kind → 决策函数` 的分派表（新增账户要在这里登记）。
11. **`step()`**：更新价格/行情快照/盘中序列；定义懒加载的 `news()`、`macro()`（10 分钟缓存，抓取成功会调用 `events.update`）、`budget_ok()`；账户按 `AI_PARAMS[..]["prio"]` 排序（D=0、E=1、C=2、F=3，规则账户=-1 最先）处理；F 首次运行写 `started`；最后 `events.maybe_curate`。
12. **`apply_events` / `fetch` / `session_open` / `sane`**：Yahoo `v8/finance/chart`（range=1y, interval=1d, events=div,splits）；陈旧报价（>30 分钟）与异常跳变（>25% 且无近期拆股）会被跳过。
13. **`run_live()` / `run_demo()`**：见 §2。演示模式用合成数据、`MockClient`、假宏观源，生成 `docs/data/state.json`（40 个交易日 × 每天 25 个 tick）。

关键设计点：
- **AI 不能越权**：数量、仓位上限、止损、T+1、费用、只做多都由代码执行；AI 的输出只是“提议”。
- **“K 对象”**：引擎产出的原因/检查文案是 `{"k":"r.tp","a":{...}}`，页面按用户语言渲染；改文案要改 `TPL`（三语）。
- **幂等与去重**：8 分钟内重复运行直接跳过；`attempts` 限制每个复盘最多 2 次；时间槽制度让晚到的运行补做。

## 6. AI 层：`bot/ai.py`（约 410 行）

### 6.1 提供商与客户端
- `providers()`：Gemini（OpenAI 兼容端点 `.../v1beta/openai`）→ 自定义备用（`AI_BASE_URL`+`AI_API_KEY`）→ GitHub Models（需 `ENABLE_GH_MODELS`）。
- `Client`：每次运行新建一个。
  - `decide(messages, payload)` → `(文本, meta)`，逐个提供商 `_try`。
  - **模型阶梯 `_try`**：首选（`state.health.ai.pinned`）→ 失败（503/429/超时/404/非法 JSON）→ `_candidates()` 列出可用 Flash 模型（稳定版→预览版→Lite，最后手段）依次尝试；5xx 先同模型重试一次；返回内容必须能 `extract_json`，否则当作失败换下一个。
  - `_call`：`reasoning_effort: low`（防思考型模型超时）、超时 120s；**请求间隔≥15 秒**（`min_gap`，防每分钟 429）；运行总预算 480s（`time_left()`）；**只有成功返回才计入 `http_calls`**（失败的 503/429 不占免费额度）。
  - **429 处理**：解析响应体（保留前 1200 字符）`quota_info()`：含 `perday` ⇒ 该模型当天作废（`dead[model]=太平洋日期`，次日自动清除，阶梯会跳过它）；每分钟类 ⇒ 按 `retryDelay`（≤50s）等一次，再换模型。所有模型都作废 ⇒ `exhausted_all`，复盘不再消耗尝试次数。`last_429` 写入 `health.ai` 供诊断，页面侧边栏显示。
  - `second_opinion()`：C 专用，另一模型给同一提示词的意见，只展示不交易。
- `MockClient`：离线演示用，规则化地模仿各类任务（决策、newstag、postmortem、digest、events、outlook）。

### 6.2 提示词结构
- `SYSTEM`（所有 AI 共用）：模拟交易、只做多；先看大局（大盘、波动、利率、行业、宏观新闻）并在 summary 第一句写判断；宏观新闻有编号 M1…（可引用）、聚合源质量参差、“谈判/计划”不是事实；HOLD 是合法答案；买入前必须写 bull/bear；JSON 严格格式（三语文本）；仓位按信念大小、不要所有仓位一样。
- `HORIZON[...]` 人设：`long`（耐心波段投资者：先判断整体趋势、约 90% 仓位、小额现金用于补仓/应急、常驻 outlook、结构性宏观冲击要提前减仓）、`short`（D：日内信号为优势，买入必须引用日内信号，风险回避日更挑剔）、`aggr`（E：高周转，bull/bear 可省略，每次≤12 条，只写 BUY/SELL）、`hedge`（F：方向优先、对冲工具规则、净暴露、不要同时重仓与反向对冲）。
- `build_messages(payload)` 用户消息的段落顺序：人设 → 时间/资金/规则 → **CURRENT INVESTED SHARE**（带目标）→ **MARKET REGIME** + **LONG-TERM VIEW** → **BIG PICTURE 指标表**（VIX、利率、美元、油、金、行业 ETF…）→ **宏观新闻（M 编号）** → **YOUR RECENT RECORD**（短线账户）→ **YOUR PREVIOUS OUTLOOK**（C）→ 持仓 → 行情表（长线=日线视角；短线/对冲=盘中视角）→ 个股新闻（N 编号）。

### 6.3 解析与校验
`extract_json` 取第一个 `{...}`；决策里的标的、动作、权重、新闻编号都在 `decide_ai` 里二次校验；`ml()` 保证三语文本结构。

### 6.4 提示词版本
改任何人设/SYSTEM 的行为含义后，bump `PROMPT_VER[horizon]`：该账户当天会**额外重审一次**（多用 1 次 AI 调用）。当前：long 5、short 5、aggr 3、hedge 0。

## 7. 学习模块：`bot/learn.py`

只观察，**绝不影响交易**，所有异常吞掉。盘后（工作日 16:00–20:00 ET、当天状态已更新）在休市分支运行 `post_close()`：
1. `store_daily_close`（记录当日收盘价，保留 60 天）、`update_outcomes`（信号的 1 日/5 日收益 r1/r5）。
2. AI 任务（每天最多 `MAX_CALLS=5` 次，失败每任务最多 2 次）：`newstag`（新闻分类：类型/情绪/重要度，用于“什么在推动价格”研究）、`postmortem`（平仓交易复盘：verdict `good_call/bad_call/lucky/unlucky/neutral` + 点评 + 要点）、`digest`（当日学习笔记：标题、要点、涨跌最大个股原因、每日一课，主题取自 `TOPICS` 20 个概念）。
3. 状态 `state.learn`：`journal`、`reviews`（≤400）、`news`、`calls`、`done`、`errors`；`state.signals` 记录每个 AI 买卖信号及后续收益，供“校准”页画图。

## 8. 新闻、宏观与要闻

- `bot/news.py`：Yahoo 个股 RSS（备用 Google News 查询）；`fetch_all(symbols, names)` 返回去重后的标题（含 `url`、`ts`、`src`）；只用于 UNIVERSE（`N` 编号）。
- `bot/macro.py`：`GAUGES`（VIX、10 年期、3 月期、HYG、TLT、美元指数、WTI、黄金、半导体/科技/金融/能源/医疗/公用/必需消费 ETF）从 Yahoo 3 个月日线算日/5 日/20 日涨跌；`TOPICS`（markets、fed、politics、ai_policy、trade、world、energy、ai_credit、ai_infra）用 Google News RSS（`when:2d`）每类取最多 3 条、48 小时内、去重，按新近排序，编号 `M1…`。**从不抛异常**，失败时返回空（`state.macro.failed_at`）。
- `bot/events.py`（“要闻与行业”页的后端）：
  - `update()`：把每次宏观抓取合并进当天列表，标题 Jaccard≥0.5 视为同一事件并累计**不同媒体数**，按 `2×媒体数+主题权重+新近度` 排序，保留 40 条/天、7 天。
  - `maybe_curate()`：每天最多 2 次 AI 精选：`am`（≥10:45 ET，盘中）、`pm`（≥16:05 ET，盘后）；输入前 12 条候选 + 指标 + 行业统计；输出 `mood`、最多 7 条 `events`（重要度 1–5、三语标题、三语“为什么重要”、影响标的）、`industries`（5 个行业组的 positive/neutral/negative + 理由）；失败每槽最多 2 次，页面回退到代码排序的英文原标题。
  - 行业分组 `GROUPS`：semis（NVDA AVGO AMD TSM ARM MU MRVL SMCI）、bigtech（AAPL MSFT AMZN GOOGL META）、software（ORCL PLTR SNOW NOW PTC）、auto（TSLA）、index（SPY QQQ）。
  - 取数是**惰性的**：只有到点的 AI 复盘才会触发 `macro()`，所以收集频率≈AI 复盘频率。

## 9. 状态文件 `state.json`（`STATE_VERSION=3`；版本不符会重置为新实验！）

```
version, initial_cash, universe[21], hedge_universe[5], updated, market_time, demo
accounts{id: {name, kind, style, freq, desc, rules[],        # 创建时从 ACCOUNTS 拷贝（页面不用它们显示）
              cash, unsettled[{amount, avail}], positions{sym:{shares, avg_cost, peak, entry, spy_px, trail_pct?, tp?, sl?}},
              pending[{side, symbol, dollars, reason, decided_at, decision_price, detail}],
              trades[{id,time,side,symbol,shares,price,fee,pnl,reason,detail{checks[],ind{},ai{},news[],weight,amount,cash_before,cash_after,spy_px,held_days?,ret?...}}],
              equity[{time, equity}],   # 近 7 天原始点，更早的压成每日一点（compact_equity）
              events[{time,symbol,type:SPLIT|DIV,...}], fees_paid, last_decision,
              ai?{done[], attempts{}, rounds[≤40], last_error, pv, deferred{}, paused?, outlook[≤40]},   # 仅 AI 账户
              instant?(E), cloned_from?(E), ver?(random), started?(F)}}
prices{sym: last}, events_seen{sym: ts}
market{sym: {t, price, prev, day, open, hi, lo, sma20, sma50, sma200, rsi, vol, sma20_5, mom20, mom60, off_high, spark[40]}}
intraday{date, px{sym:[≤30 个当日价点]}}
health{last_ok, last_attempt, last_error, fail_count, ai{configured[], pinned{provider:model}, last_ok, last_error, last_429{model,quota,per_day,retry,t}, dead{model:太平洋日期}}}
ai_usage{日期: 成功调用次数, limit_hint?}        # 保留 30 天
macro{t, gauges[{sym,name,kind,price,d1,d5,d20}], headlines[{id,sym,topic,title,src,url,ts,age_h}], failed_at?}
events{groups, days{日期:{items[{title,src,srcs[],topic,url,first,tk[]}]}}, curated{日期:{am|pm:{t,model,mood,events[],industries[],stats[]}}}, errors{}, last_error?}
learn{journal[], reviews{}, news[], calls{}, done{}, errors{}, last_error}
signals[{id,time,date,acct,sym,action,conf,applied,px,r1,r5}], signal_seq, daily_close{日期:{sym:价}}
```
- 实盘状态在 `data` 分支的 `state.json`；`docs/data/state.json` 只是**演示数据**（`python bot/run.py --demo` 生成，提交在仓库里；页面检测到 `demo:true` 会显示“离线演示数据”横幅）。
- 状态体积：约 3.5 MB（40 天演示）。`equity` 压缩、`rounds`/`reviews` 有上限。

## 10. 看板：`docs/index.html`（单文件，约 800 行，行很长）

### 10.1 结构
- 无构建、无框架；`<script>` 里按块顺序：i18n → 账户文案 → 工具函数 → 图表 → 各页面 → 路由/轮询。
- **i18n**：`D`（字典）+ `add(key, zh, en, fr)` + `t(key, args)`（缺 key 会记入 `window.__missing`，可在控制台检查翻译缺失）；`T(x)` 渲染数据文本（字符串 / `{k,a}` 模板 / `{zh,en,fr}`）；`TPL` 数组是引擎 `K()` 模板的三语表；`AC` 是每个账户的名称/风格/频率/说明/规则三语文案；`GL` 是学习中心词典。语言存在 `localStorage`，设置在侧边栏底部的弹出菜单。
- **数据流**：加载 `data/state.json` → `S`；`poll()` 每 30 秒拉一次，文本变化才重绘（若页面输入框有焦点则只更新导航）；`pageTag()` 用 ETag 检测页面本身是否有新版，有则提示并自动刷新。
- **路由**（hash）：`#/overview`、`#/compare`、`#/news`、`#/learn[/tab[/term]]`、`#/<账户id>`；`route()` 白名单在 `ROUTE=(S.accounts[r]||r==='compare'||r==='learn'||r==='news')?r:'overview'`，`render()` 分派到各页面函数。
- **页面函数**：`overview()`（排名表、净值曲线、持仓卡片、大环境卡片 `macroCard`、行情表）、`comparePage()`（2×2 矩阵、结论 `verdicts()`、指标表 `stats()`、持仓重叠、AI 分析、校准）、`newsPage()`（要闻与行业）、`traderPage(id)`（核心洞察 `insights`、候选表 `candidates`、大势判断 `outlookCard`、持仓/待成交/决策日志、AI 复盘记录、点击弹窗 `detail()`）、`learnHub()`（日志/新闻影响/校准/你 vs AI 挑战/词典）。
- **侧边栏**：导航 `nav()`；顶部信息区是**一行滚动提示**（`infoItems()` 组装 → `tickerSet/tickerShow` 轮播，过长的行横向滚动，鼠标悬停暂停，小圆点可跳转）；导航滚动条被隐藏（底部渐隐，滚轮/触摸仍可滚）；设置按钮在底部。
- **常量**：`TRADERS`（交易员 id 列表，顺序即导航顺序）、`COLORS`（每账户颜色）、`LTABS`、`POLL_MS=30s`。
- **localStorage 键**：语言、主题、时区、`sim_picks_v1`（“你 vs AI”挑战的选择）。所有读写都包 try/catch。
- 样式：CSS 变量（`--bg/--card/--fg/--mut/--line/--up/--down/--acc/--hl`），自动深浅色，手机宽度下导航变横向条。

### 10.2 常见改动要碰的地方
| 要做的事 | 要改 |
|---|---|
| 新增交易员 | `bot/run.py`：`ACCOUNTS`、`AI_PARAMS`（或决策函数）、`KIND`、`new_account`/`migrate`（老状态补建）；`bot/ai.py`：`HORIZON`、`build_messages` 里的目标表；`bot/learn.py`：`TRADERS/EN_NAME`；页面：`AC`、`TRADERS`、`COLORS`、`comparePage` 里几处 id 列表（`ai`、`fast`、`aiIds`、矩阵行）、`mx.*` 翻译；EXPERIMENT.md。 |
| 新增页面/标签 | `route()` 白名单、`render()` 分派、`nav()` 加链接、翻译 `add('nav.xxx',…)`。 |
| 改文案 | 三语一起改；引擎文案用 `K()` 并在 `TPL` 加条目；账户说明改 `ACCOUNTS`（Python）和 `AC`（JS）两处。 |
| 新增指标 | `stats()` + `rowsDef`（比较页）+ 对应 `m.*` 翻译。 |

## 11. 页面与数据的对应（排查“页面不对”时）
- 总览排名 = `stats/ret` 基于 `accounts[*].equity` 最后一点 / `initial_cash`。F 的收益从它自己的起点算（名称旁标“自 X 起”）。
- “大环境”卡片 = `state.macro`；“要闻与行业” = `state.events`（有 `curated` 用 AI 精选，否则用 `days[日期].items`）；行业统计优先用精选里存的 `stats`，否则用 `state.market` 实时算。
- C 页面“大势判断” = `accounts.ai_long.ai.outlook`。
- 侧边栏滚动提示 = `health.ai`、`ai_usage`、`last_429`、`dead`。

## 12. 运维手册（排障）

| 现象 | 先查 | 常见原因/处理 |
|---|---|---|
| 页面数据不更新 | `data` 分支最后提交时间；trade 运行是否成功；`health.last_error` | 链断了（看 tick 列表）；Pages 部署失败（只影响页面，数据仍在）；浏览器旧版页面（Ctrl+Shift+R） |
| 链断了 | `gh`/MCP 列出 `tick.yml` 运行；是否有一个 `in_progress` 的 tick | 外部取消会靠备用 cron 复活；手动在 Actions 里运行 `tick` 或 `trade` 即可（同一时间只会有一条链） |
| AI 报错横幅 | `health.ai.last_error`、`last_429`、`dead` | 503=模型过载（自动换模型）；429 perday=该模型当天作废（次日恢复）；全部作废时复盘等待；可加备用服务商 |
| 某个复盘没做 | `accounts.*.ai.done/attempts/deferred/paused` | 顺延一次（正常）；额度预算暂停（E/F）；尝试 2 次都失败 |
| 宏观/要闻为空 | `state.macro.failed_at`、`events.errors` | Yahoo/Google 抓取失败；`events.update` 内部吞异常，需在沙箱外复现 |
| 交易异常 | `decisions/<日期>.jsonl`（`data` 分支）里有完整提示词、原始回答和每条决策的代码裁决（`applied`/`note`） | 提示词问题→改提示词并 bump `PROMPT_VER` |
| 手动重试 AI | 手动运行 `trade`（非 chained）会设置 `RETRY_AI=1`，清空当天尝试次数 | 仅用于排障 |

AI 额度现状（重要）：免费 Gemini 的**每个模型每天都有较小的上限**，经常在交易日中途全部用完（2026-11-13 八个模型在中午前后全部作废，当天成功调用 26 次）。目前靠自动换模型、间隔 15 秒、碰撞顺延、F 优先暂停来撑。用户尚未提供 AI Studio “Rate limits” 页面的真实数字。若复盘开始频繁漏掉：优先考虑精简调用（去掉 C 的第二位分析师、减少学习任务、E 减档），或让用户注册一个备用免费服务商（Groq/OpenRouter）并设置 `AI_BASE_URL/AI_API_KEY/AI_MODEL`。

## 13. 开发配方（怎么改、怎么验）

没有单元测试框架，用下面几种办法：

```bash
# 1) 离线演示：生成 docs/data/state.json（会改动该文件；提交前用它刷新，或先 git checkout 再重跑）
python bot/run.py --demo

# 2) JS 语法检查（页面是单个 <script>）
node -e "const h=require('fs').readFileSync('docs/index.html','utf8');const m=h.match(/<script>([\s\S]*)<\/script>\s*<\/body>/);new Function(m[1]);console.log('js ok')"

# 3) 本地预览 + Playwright 渲染检查（Playwright 与 Chromium 已预装，直接 require('playwright')）
(cd docs && python3 -m http.server 8765 &)   # 然后用 node 脚本打开 http://localhost:8765/#/news 等并收集 pageerror
# 4) 单测某个函数：python3 -c "import sys; sys.path.insert(0,'bot'); import run; ..."（用 MockClient / 手写 state）

# 5) 读线上状态（不要用沙箱去访问 Pages）
git fetch origin data && git show origin/data:state.json > /tmp/s.json
```
- 查 Actions 用 GitHub MCP 工具（`mcp__github__actions_list`、`get_job_logs`；会话里这些工具可能断开重连，需要 `ToolSearch` 重新加载 schema）；沙箱里没有 `gh` 可用于 Actions。
- **改文件的安全做法**：批量/带引号的改动写成脚本文件再运行（`Write` 一个 patch 脚本，里面 `assert old in s` 再 `replace`），不要在 shell heredoc 里嵌套引号；中文和三语文案放脚本变量里。不要把 `pkill` 和 `git` 命令串在同一条命令里（会连带杀掉 shell）。
- 提交流程：改完 → `--demo` 通过 → JS 检查通过 → 更新 EXPERIMENT.md（日期小节：发现/依据/改动）→ `git add -A && git commit && git push`。`docs/data/state.json` 会随 demo 改变，属正常。
- 用 `mcp__claude-code-remote__send_later` 给自己安排“开盘后检查”之类的复查；复查内容写在消息里（读 `data` 分支的哪些键、期望是什么、出错怎么修）。

## 14. 已踩过的坑（别再踩）

1. **提示词一改行为就变**：C 曾因“别追高、等回调”长期持 60% 现金；现在目标仓位写在提示词里并随当前投入比例回显。改提示词必须 bump `PROMPT_VER`。
2. **强制清仓会伤策略**：曾经给 B/D 加“15:30 强制清仓”，AI 因此在亏损时被迫卖出；已取消（可隔夜）。
3. **429 的错误体被截断**导致无法区分每分钟/每天，已保留 1200 字符并解析；“一个模型当天额度用完”不等于“总额度用完”。
4. **预算保险别估得太保守**：E 曾被自设的“每天 60 次（含失败请求）”误停；现在只计成功调用，默认上限 200。
5. **同一时刻多个账户一起请求 AI** 会触发每分钟 429：时间表错开 + 15 秒间隔 + 碰撞顺延。
6. **cron 不靠谱**：自己做接力链；备用 cron 只是复活。
7. **等权对照组不能含对冲标的**，否则基线改变；对冲标的只为 F 抓取并进入行情快照。
8. **`regime_rows` 必须只统计 UNIVERSE**（否则对冲 ETF 会污染“上升趋势股票数”）。
9. **演示模式**：提示/函数里不要依赖真实网络；`MockClient` 要覆盖新增任务类型，否则演示页面没有数据。
10. **状态版本**：改 `STATE_VERSION` 会让线上状态重置，非常危险，只有明确要重开实验才改；日常升级用 `migrate()`。
11. **页面文案有两份来源**：`ACCOUNTS`（Python，存进 state，仅备用）和 `AC`（JS，页面实际显示），还有 `TPL`；曾经出现 B 的卖出标签仍写“止盈 +1.0%”的陈旧文案。
12. **`events.update` 与宏观抓取都吞异常**，出错时页面只是“没数据”——验证时要读 `state.macro/failed_at`、`events.errors`。
13. 对冲 ETF（SH/PSQ）每日重置、长期持有会损耗，提示词限定“几小时到几天”。

## 15. 变更与证据索引
- 详细的、按日期的变更日志、每次校准的依据（含胜率/盈亏平衡数据）、关于“Nvidia 担保融资”说法的核实结论，见 `EXPERIMENT.md`。
- 关键里程碑（2026-11）：11-02 实验开始（A–D + 对照）；11-03 加入 E（克隆 D）；11-04 大量修复（接力链 tick、AI 自动换模型、仓位策略、页面自动刷新）；11-05 市场暴跌（SPY 约 -4.4%）；11-06 修复 429/部署重试；11-09 宏观大环境数据 + C 的大势判断；11-13 “要闻与行业”页；11-14 校准（结构性新闻、短线胜率提示、B 的 VIX 停手）+ 账户 F；侧边栏滚动提示。

## 16. 未决事项与想法
- 用户还没确认“正式对比起点”；F 从 11-16 才开始，对比必须看同期。
- 真实的 AI 每日额度未知（等用户提供 Rate limits 截图）；是否需要备用服务商取决于漏复盘的频率。
- 短线账户的入场质量是最大的短板（胜率低于盈亏平衡线）：看 11-14 校准后的几周数据再决定是否继续收紧（例如降低换手、限制下跌日入场）。
- 想法（未做）：对比页加“收益 ÷ 实际投入资金”；A 的“赢家加仓”；事件触发的额外复盘；把“要闻”按行业映射到持仓；真实费率/借券成本的做空账户（目前 F 只用反向 ETF）。
- 已知局限：只做多、碎股、无融资融券；行情为 15 分钟节奏的 Yahoo 数据，日内策略被这个节奏限制；样本期很短，任何结论都需要数周数据。
