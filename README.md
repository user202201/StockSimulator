# StockSimulator
美股模拟交易实验：GitHub Actions 定时运行机器人，GitHub Pages 展示看板。详见 [EXPERIMENT.md](EXPERIMENT.md)。

- `bot/run.py`：行情获取（Yahoo）、多账户引擎、规则策略、模拟成交
- `docs/`：静态看板（含内置 Chart.js），`docs/data/state.json` 仅为离线演示数据
- 机器人的真实数据存放在 `data` 分支（`state.json`），每次运行后随看板一起发布

## 本地预览
```bash
python bot/run.py --demo        # 生成演示数据
python -m http.server -d docs   # 浏览器打开 http://localhost:8000
```

## AI 服务（交易员 C、D 需要）
机器人按顺序尝试已配置的服务商：自定义 → Gemini → GitHub Models。**建议配置 Gemini 免费密钥**（GitHub Models 可能已下线）：
1. 到 https://aistudio.google.com/apikey 创建免费 API Key
2. 仓库 Settings → Secrets and variables → Actions → New repository secret，名称 `GEMINI_API_KEY`
3. 可选：Variables 里设 `GEMINI_MODEL`（默认 `gemini-2.5-flash`）
没有配置时 A、B 和对照账户照常运行，C、D 暂不交易，看板会提示。

**自动切换模型**：Gemini 出问题（过载 503、限流、超时、模型下线、返回的不是合法 JSON）时，会按顺序自动换：最新的稳定版 Flash → 预览版 Flash → Flash-Lite（最后手段）。同一个密钥下的各个型号额度各算各的。换了哪个模型会记在每条决策里。
**可选的备用服务商**：想防“Google 整体出问题”，可以再注册一个免费服务（如 OpenRouter、Groq），在仓库 Secrets 里加 `AI_BASE_URL`、`AI_API_KEY`，Variables 里加 `AI_MODEL`；Gemini 全部失败时才会用它。

## 上线
1. 合并到默认分支（Actions 定时任务只在默认分支运行）
2. Settings → Pages → Source 选 **GitHub Actions**
3. Actions → trade → Run workflow

## 运行机制（自动接力）
GitHub 的定时任务（cron）经常延迟几小时，所以不依赖它：
- `trade.yml`：真正干活（行情、决策、成交、发布网页），一次约 1 分钟。结束时把接力棒交给 `tick.yml`。
- `tick.yml`：计时器。交易时段每 15 分钟叫醒一次 `trade`；休市时（夜里、周末）每次最多睡 5 小时再叫醒下一个 `tick`，最后一棒在下一个交易日美东 9:30:45 叫醒 `trade`。链条永不停止，不需要手动启动。
- 保险：`trade`/`tick` 的 cron、`keepalive`（每周）都会重新叫醒 `tick`；同一时间只会有一条链（并发组保证）。
