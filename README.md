# 🐋 Whale Alert Service — 庄家控盘预警系统

## 架构

```
whale-alert-service/
├── config.py              # 配置中心 (API密钥/代币列表/阈值)
├── main.py                # 主入口 (调度器)
├── data/
│   ├── binance_client.py  # Binance REST + WebSocket 数据采集
│   ├── onchain_client.py  # 链上数据 (Etherscan / BscScan / Dune)
│   └── data_store.py      # SQLite 本地存储
├── analysis/
│   ├── indicators.py      # 技术指标计算
│   ├── whale_detector.py  # 庄家控盘特征检测引擎
│   └── alert_engine.py    # 预警决策引擎 (仅在高概率时触发)
├── alerts/
│   ├── telegram_bot.py    # Telegram 推送
│   ├── webhook.py         # 通用 Webhook 推送
│   └── logger.py          # 本地日志记录
├── web/
│   └── dashboard.py       # FastAPI 仪表盘 API
├── requirements.txt
└── README.md
```

## 核心特性

1. **7维控盘评分模型**：缩量横盘·大单占比·买卖不平衡·链上净流出·对倒交易·筹码集中·价差异常
2. **仅在高概率时预警**：控盘分 ≥70 且处于"吸筹末期/即将拉盘"阶段才触发通知
3. **多数据源融合**：Binance K线/深度/成交 + Etherscan/BscScan 链上转账 + 持仓分布
4. **Telegram 实时推送**：预警消息直达手机
5. **REST API 仪表盘**：浏览器查看所有代币控盘状态

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 (编辑 config.py)
#    - 填入 Binance API Key (只需读权限)
#    - 填入 Etherscan/BscScan API Key
#    - 填入 Telegram Bot Token + Chat ID
#    - 调整监控代币列表和预警阈值

# 3. 启动
python main.py

# 4. (可选) 仅启动仪表盘 API
python -m web.dashboard
```

## 预警触发条件 (全部满足才报警)

- 控盘综合评分 ≥ 70/100
- 阶段判定为 "吸筹末期" 或 "即将拉盘"
- 至少 3 个维度同时触发高分信号
- 非重复预警 (同一代币 4 小时内不重复)

## ⚠️ 风险提示

本系统仅供技术研究，不构成投资建议。庄家行为本质上不可精确预测。
