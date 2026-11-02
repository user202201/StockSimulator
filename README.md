# StockSimulator
US-stock paper trading bot running on GitHub Actions (free). Dashboard: `docs/` via GitHub Pages.
- `bot/run.py` – data fetch (Yahoo), trend-following strategy, simulated fills
- `docs/` – static dashboard + `docs/data/state.json` (bot state, committed by Actions)
- Preview offline: `python bot/run.py --demo && python -m http.server -d docs`
