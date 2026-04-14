"""
配置中心 — 所有可调参数集中管理
"""
import os
from dataclasses import dataclass, field
from typing import List, Dict

# ═══════════════════════════════════════════════════════════════════════════
# API 密钥 (建议使用环境变量, 此处为 fallback)
# ═══════════════════════════════════════════════════════════════════════════

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# Etherscan / BscScan (免费 tier 5 calls/sec)
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")

# Telegram 推送
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 通用 Webhook (Slack / Discord / 飞书等)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# ═══════════════════════════════════════════════════════════════════════════
# 稳定币过滤 (不参与控盘分析)
# ═══════════════════════════════════════════════════════════════════════════

STABLECOIN_SYMBOLS = {
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "DAIUSDT",
    "USDDUSDT", "USDPUSDT", "USDEUSDT", "RLUSDUSDT", "BFUSDUSDT",
    "XUSDUSDT", "EURUSDT", "EURIUSDT", "PAXGUSDT", "XAUTUSDT",
    "PYUSDUSDT", "FRAXUSDT", "LUSDUSDT", "USTCUSDT", "USDMUSDT",
    "USDJUSDT", "SUSDUSDT", "DAIUSDT",
}

# ═══════════════════════════════════════════════════════════════════════════
# 监控代币列表
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TokenConfig:
    symbol: str              # Binance 交易对, e.g. "PEPEUSDT"
    name: str                # 显示名
    chain: str = "bsc"       # 链: "eth" / "bsc" / "sol" / "arb"
    contract: str = ""       # 代币合约地址 (用于链上查询)
    decimals: int = 18
    tags: List[str] = field(default_factory=list)  # 标签: meme, defi, ai, l2 等

WATCH_TOKENS: List[TokenConfig] = [
    # ── Meme 币 (高控盘概率) ──
    TokenConfig("PEPEUSDT", "Pepe", "eth", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", 18, ["meme"]),
    TokenConfig("WIFUSDT", "dogwifhat", "sol", "", 6, ["meme"]),
    TokenConfig("FLOKIUSDT", "Floki", "bsc", "0xfb5B838b6cfEEdC2873aB27866079AC55363D37E", 9, ["meme"]),
    TokenConfig("BONKUSDT", "Bonk", "sol", "", 5, ["meme"]),
    TokenConfig("SHIBUSDT", "Shiba Inu", "eth", "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", 18, ["meme"]),
    TokenConfig("DOGEUSDT", "Dogecoin", "bsc", "", 8, ["meme"]),
    TokenConfig("TURBOUSDT", "Turbo", "eth", "0xA35923162C49cF95e6BF26623385eb431ad920D3", 18, ["meme"]),
    TokenConfig("NEIROUSDT", "Neiro", "eth", "", 18, ["meme"]),
    TokenConfig("NOTUSDT", "Notcoin", "ton", "", 9, ["meme"]),

    # ── 山寨 (中等控盘概率) ──
    TokenConfig("SUIUSDT", "Sui", "sui", "", 9, ["l1"]),
    TokenConfig("TIAUSDT", "Celestia", "eth", "", 6, ["modular"]),
    TokenConfig("FETUSDT", "Fetch.ai", "eth", "0xaea46A60368A7bD060eec7DF8CBa43b7EF41Ad85", 18, ["ai"]),
    TokenConfig("INJUSDT", "Injective", "eth", "", 18, ["defi"]),
    TokenConfig("ARBUSDT", "Arbitrum", "arb", "", 18, ["l2"]),
    TokenConfig("OPUSDT", "Optimism", "eth", "0x4200000000000000000000000000000000000042", 18, ["l2"]),
    TokenConfig("APEUSDT", "ApeCoin", "eth", "0x4d224452801ACEd8B2F0aebE155379bb5D594381", 18, ["nft"]),
    TokenConfig("GALAUSDT", "Gala", "eth", "0xd1d2Eb1B1e90B638588728b4130137D262C87cae", 8, ["gaming"]),
    TokenConfig("IMXUSDT", "Immutable", "eth", "0xF57e7e7C23978C3cAEC3C3548E3D615c346e79fF", 18, ["gaming"]),
    TokenConfig("BLURUSDT", "Blur", "eth", "0x5283D291DBCF85356A21bA090E6db59121208b44", 18, ["nft"]),
    TokenConfig("ORDIUSDT", "ORDI", "btc", "", 18, ["btc_eco"]),
    TokenConfig("1000SATSUSDT", "1000SATS", "btc", "", 18, ["btc_eco"]),
    TokenConfig("PEOPLEUSDT", "ConstitutionDAO", "eth", "0x7A58c0Be72BE218B41C608b7Fe7C5bB630736C71", 18, ["dao"]),
    TokenConfig("LUNCUSDT", "Terra Classic", "terra", "", 6, ["zombie"]),
]

# ═══════════════════════════════════════════════════════════════════════════
# 控盘检测参数 — 可微调的阈值
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DetectionThresholds:
    """
    控盘检测阈值 — 校准目标: 预测 24h ≥30% 暴涨 / 4h ≥50% 暴跌

    30%/24h 是中高强度事件:
    - 比50%宽松, 可以适当放宽信号要求
    - 出货信号 (暴跌预测) 与吸筹信号相反
    """

    # ── 缩量横盘 (吸筹特征) ──
    vol_shrink_critical: float = 0.35
    vol_shrink_high: float = 1.5
    price_range_narrow: float = 0.05
    price_range_medium: float = 0.10

    # ── 大单占比 ──
    large_order_critical: float = 40.0
    large_order_high: float = 25.0
    large_order_threshold: float = 50000

    # ── 买卖盘不平衡 ──
    imbalance_critical: float = 0.68
    imbalance_high: float = 0.58

    # ── 链上净流出 ──
    net_outflow_critical: int = 250
    net_outflow_high: int = 100

    # ── 对倒交易 ──
    wash_trade_critical: float = 30.0
    wash_trade_high: float = 15.0

    # ── 持仓集中度 ──
    holder_decrease_critical: int = 25
    holder_decrease_high: int = 10
    top10_hold_critical: float = 55.0

    # ── 价差异常 ──
    spread_narrow_critical: float = 0.08

    # ── 资金费率 ──
    funding_rate_bearish: float = 0.001    # >0.1% = 多头拥挤
    funding_rate_bullish: float = -0.0005  # <-0.05% = 空头拥挤
    funding_rate_critical: float = -0.001  # <-0.1% = 极端空头

    # ══ 出货/暴跌信号阈值 ══
    # 放量暴跌: 突然放量但价格下跌
    dump_vol_surge_critical: float = 4.0   # 成交量突增 4x + 下跌
    dump_vol_surge_high: float = 2.5
    # 链上净流入: 筹码涌入交易所 (准备砸盘)
    net_inflow_critical: int = 200         # 净流入 >200笔
    net_inflow_high: int = 80
    # 卖盘压力
    sell_pressure_critical: float = 0.72   # 卖盘占比 >72%
    sell_pressure_high: float = 0.62
    # 持仓地址暴增 (散户接盘)
    holder_increase_critical: int = 50
    holder_increase_high: int = 20

THRESHOLDS = DetectionThresholds()

# ═══════════════════════════════════════════════════════════════════════════
# 预警触发条件 — 暴涨: 30%/24h | 暴跌: 50%/4h
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AlertConfig:
    """暴涨预警条件"""
    min_control_score: int = 50  # Raised from 35 (2026-04-14): 478 alerts at 35 → 335 FPs (70%); 50 filters noise phases
    min_signal_count: int = 3
    required_phases: List[str] = field(
        default_factory=lambda: [
            "吸筹末期",    # Late accumulation — strong evidence
            "即将拉盘",    # Imminent pump — strongest signal
            "高度控盘",    # Heavy manipulation confirmed
            # Removed: "疑似吸筹", "中度控盘", "轻度异常" — these 3 generated
            # the bulk of 335 false positives; not reliable trade entry phases
        ]
    )
    cooldown_hours: float = 4.0
    min_pump_probability: int = 30  # Raised from 25 — with circular prob formula, 25% = near-zero real conviction

@dataclass
class CrashAlertConfig:
    """暴跌预警条件"""
    min_crash_score: int = 65          # 出货评分 ≥65
    min_signal_count: int = 3          # 至少 3 个出货信号
    required_phases: List[str] = field(
        default_factory=lambda: [
            "出货末期",
            "即将砸盘",
            "高度出货",
        ]
    )
    cooldown_hours: float = 2.0        # 暴跌预警冷却短 (紧急)
    min_crash_probability: int = 50    # 最低暴跌概率

ALERT_CONFIG = AlertConfig()
CRASH_ALERT_CONFIG = CrashAlertConfig()

# ═══════════════════════════════════════════════════════════════════════════
# Position Scaling (Pyramiding) — 加仓策略
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PositionScalingConfig:
    """Position scaling parameters - Add to existing positions when scores improve"""

    # Limits
    max_position_usd: int = 1000              # Max total position size ($)
    max_scale_ins: int = 2                    # Max additional entries (3 total)
    min_scale_interval_min: int = 30          # Min minutes between entries

    # Score requirements
    min_score_jump: int = 15                   # Min score increase to trigger scale-in
    min_score_absolute: int = 60               # Min absolute score for scale-in

    # Price limits
    max_price_change_pct: float = 2.0         # Max % price change from initial entry

    # Position sizing (pyramid: decrease size with each addition)
    scale_in_percent: list = field(default_factory=lambda: [0.5, 0.33])  # 50%, then 33%

    # Risk controls
    max_drawdown_pct: float = 8.0             # Max 8% drop → auto exit
    time_stop_hours: float = 6.0              # Exit after 6h if stagnant
    volatility_stop_pct: float = 5.0          # Exit if 5% swing in 15min

POSITION_SCALING = PositionScalingConfig()

# ═══════════════════════════════════════════════════════════════════════════
# 运行参数
# ═══════════════════════════════════════════════════════════════════════════

# 数据采集周期
SCAN_INTERVAL_MINUTES = 15       # 全量扫描间隔 (分钟)
KLINE_INTERVAL = "1h"            # K线周期
KLINE_LOOKBACK_DAYS = 90         # K线回溯天数
ORDERBOOK_DEPTH = 20             # 深度档位数

# 数据库
DB_PATH = "whale_alert.db"

# Web API
WEB_HOST = "0.0.0.0"
WEB_PORT = 8888

# 日志
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
