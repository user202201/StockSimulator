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

## 上线
1. 合并到默认分支（Actions 定时任务只在默认分支运行）
2. Settings → Pages → Source 选 **GitHub Actions**
3. Actions → trade → Run workflow
