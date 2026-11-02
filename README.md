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

## 上线
1. 合并到默认分支（Actions 定时任务只在默认分支运行）
2. Settings → Pages → Source 选 **GitHub Actions**
3. Actions → trade → Run workflow
