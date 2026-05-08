# Whale Alert Service

庄家控盘预警 + Top-10 混合策略服务。

当前实现不是单一路径的“高控盘分才报警”系统，而是两条并行路由：

- `trend` 路由：`BTCUSDT` / `ETHUSDT` 走大盘趋势评估与 `MAINSTREAM` 交易档位
- `whale` 路由：其余 Top-10、配置 watchlist、自动发现代币继续走控盘/吸筹检测

## 架构

```text
whale-alert-service/
├── main.py                   # 主服务: 扫描、实时信号、Top-10 刷新
├── config.py                 # 监控列表、阈值、MAINSTREAM 配置
├── analysis/
│   ├── alert_engine.py       # hybrid 预警路由 (trend / whale)
│   ├── indicators.py         # 技术指标
│   └── whale_detector.py     # 控盘特征检测
├── data/
│   ├── auto_discovery.py     # 自动发现 + watchlist 清理
│   ├── binance_client.py     # Binance REST / WS 数据采集
│   ├── data_store.py         # SQLite 存储
│   └── top_coins_fetcher.py  # CoinGecko Top-10 获取与 fallback
├── trading/
│   └── paper_trader.py       # 模拟交易与 tier 风控
├── web/
│   └── dashboard.py          # FastAPI dashboard + REST API
└── tests/
```

## Watchlist 语义

- `WATCH_TOKENS` 中静态配置的代币视为手动 watchlist，默认受保护，不会被自动清理移除
- Top-10 市值代币会在启动时注入 watchlist，并在 24 小时刷新后自动补订阅 realtime feed
- 自动发现加入的代币标记为 `auto`，在不活跃时允许被清理
- 仪表盘会把每个代币标注为 `Configured`、`Top-10`、`Configured + Top-10` 或 `Auto`

## 预警与交易路由

### 1. Trend 路由

- 适用：`BTCUSDT`、`ETHUSDT`
- 预警：允许低 legacy `control_score` 的趋势信号进入新逻辑
- 交易：直接归类为 `MAINSTREAM` tier，使用更紧的止损止盈参数

### 2. Whale 路由

- 适用：其余 Top-10、大部分 meme / alt / 自动发现代币
- 预警：基于控盘分、阶段、信号数、冷却时间等规则
- 交易：继续按 `strong / medium / weak` tier 执行

## Dashboard

启动后访问 `http://<host>:8888/`。

仪表盘包含：

- `Overview`：Top-10 hybrid routing、受保护 watchlist、24h alerts、PnL
- `Positions`：open / closed paper trades，含 `MAINSTREAM` tier 展示
- `Tokens`：每个快照的 route、source、protected 状态
- `Stats`：paper trading、pump detection、recent alerts

主要接口：

- `GET /api/tokens`
- `GET /api/watchlist`
- `GET /api/alerts`
- `GET /api/trades/open`
- `GET /api/trades/closed`
- `GET /api/trades/stats`
- `GET /api/discovery/stats`

## 快速启动

```bash
# 1. 安装依赖
python3 -m pip install -r requirements.txt

# 2. 配置环境变量或直接修改 config.py
export BINANCE_API_KEY=...
export BINANCE_API_SECRET=...
export ETHERSCAN_API_KEY=...
export BSCSCAN_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...

# 3. 启动服务
python3 main.py

# 4. 仅启动 dashboard
python3 -m web.dashboard
```

## 运行说明

- Top-10 列表优先从 CoinGecko 获取，失败时回退到内置 fallback 列表
- Top-10 cache 每 24 小时刷新一次
- realtime fast-signal 与主扫描现在共用同一套 `strategy_map` / `indicators_map`
- 若首次切换 coin universe，需要时可设置 `ML_RESET_ON_STARTUP=true`

## 测试

```bash
python3 -m unittest tests.test_review_regressions tests.test_dashboard_api
```

## 风险提示

本项目只用于研究与策略验证，不构成投资建议。链上、盘口和趋势信号都可能失真，必须结合人工判断与独立风控。
