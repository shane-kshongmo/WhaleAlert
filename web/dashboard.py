"""
FastAPI 仪表盘 API — 增强版
提供 REST 接口查看所有代币控盘状态、预警历史、模拟交易
"""
import json
import time
import logging
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from data.data_store import DataStore
from config import WEB_HOST, WEB_PORT

logger = logging.getLogger(__name__)

app = FastAPI(title="Whale Alert Dashboard", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

store = DataStore()

# 延迟初始化, 供外部注入
_token_manager = None
_pump_monitor = None
_scanner = None
_trader = None


def set_managers(token_mgr, pump_mon, scanner=None, trader=None):
    global _token_manager, _pump_monitor, _scanner, _trader
    _token_manager = token_mgr
    _pump_monitor = pump_mon
    _scanner = scanner
    _trader = trader


# ══════════════════════════════════════════════════════════════════════
# Token API
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/tokens")
async def get_all_tokens():
    """获取所有代币最新控盘状态"""
    snapshots = store.get_all_latest()
    results = []
    for s in snapshots:
        signals = json.loads(s.get("signals_json", "[]")) if s.get("signals_json") else []
        metrics = json.loads(s.get("metrics_json", "{}")) if s.get("metrics_json") else {}
        results.append({
            "symbol": s["symbol"],
            "price": s.get("price", 0),
            "change_24h": s.get("change_24h", 0),
            "control_score": s.get("control_score", 0),
            "phase": s.get("phase", ""),
            "pump_probability": s.get("pump_probability", 0),
            "signals": signals,
            "metrics": metrics,
            "timestamp": s.get("timestamp", 0),
        })
    return {"tokens": results, "count": len(results)}


@app.get("/api/token/{symbol}")
async def get_token_detail(symbol: str, limit: int = Query(50, ge=1, le=500)):
    """获取单个代币历史快照"""
    snapshots = store.get_snapshots(symbol.upper() + "USDT", limit)
    if not snapshots:
        snapshots = store.get_snapshots(symbol.upper(), limit)
    return {"symbol": symbol, "snapshots": snapshots, "count": len(snapshots)}


@app.get("/api/alerts")
async def get_alerts(hours: int = Query(24, ge=1, le=720)):
    """获取最近预警记录"""
    alerts = store.get_recent_alerts(hours)
    return {"alerts": alerts, "count": len(alerts), "hours": hours}


@app.get("/api/alerts/critical")
async def get_critical_alerts():
    """获取当前所有高分代币 (控盘分>=50)"""
    snapshots = store.get_all_latest()
    critical = [s for s in snapshots if s.get("control_score", 0) >= 50]
    return {"critical_tokens": critical, "count": len(critical)}


# ══════════════════════════════════════════════════════════════════════
# Token Management API
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/tokens/add")
async def add_token(symbol: str, name: str = "", chain: str = "eth", contract: str = ""):
    if not _token_manager:
        return {"success": False, "message": "Token manager not initialized"}
    return _token_manager.add_token(symbol, name, chain, contract)


@app.post("/api/tokens/remove")
async def remove_token(symbol: str):
    if not _token_manager:
        return {"success": False, "message": "Token manager not initialized"}
    return _token_manager.remove_token(symbol)


@app.get("/api/tokens/list")
async def list_watched_tokens():
    if not _token_manager:
        return {"tokens": [], "count": 0}
    tokens = _token_manager.list_tokens()
    return {"tokens": tokens, "count": len(tokens)}


@app.post("/api/tokens/batch")
async def add_tokens_batch(tokens: list):
    if not _token_manager:
        return {"success": False, "message": "Token manager not initialized"}
    return _token_manager.add_tokens_batch(tokens)


# ══════════════════════════════════════════════════════════════════════
# Paper Trading API
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/trades/open")
async def get_open_trades():
    """获取所有未平仓位"""
    if not _trader:
        return {"trades": [], "count": 0}
    trades = _trader.get_open_positions()
    return {"trades": trades, "count": len(trades)}


@app.get("/api/trades/closed")
async def get_closed_trades(limit: int = Query(50, ge=1, le=500)):
    """获取已平仓历史"""
    if not _trader:
        return {"trades": [], "count": 0}
    trades = _trader.get_recent_trades(limit)
    closed = [t for t in trades if t.get("status") == "closed"]
    return {"trades": closed, "count": len(closed)}


@app.get("/api/trades/stats")
async def get_trade_stats(days: int = Query(30, ge=1, le=365)):
    """获取交易统计"""
    if not _trader:
        return {}
    return _trader.get_stats(days)


@app.post("/api/trades/close/{trade_id}")
async def close_trade(trade_id: int):
    """手动平仓"""
    if not _trader:
        return {"success": False, "message": "Trader not initialized"}
    with _trader.store._conn() as conn:
        row = conn.execute("SELECT * FROM paper_trades WHERE id=? AND status='open'", (trade_id,)).fetchone()
        if not row:
            return {"success": False, "message": f"Trade #{trade_id} not found or already closed"}
    # Get latest price from snapshot
    symbol = row["symbol"]
    snap = store.get_latest_snapshot(symbol)
    if not snap or not snap.get("price"):
        return {"success": False, "message": f"No price data for {symbol}"}
    current_price = snap["price"]
    result = _trader.close_position(trade_id, current_price, reason="manual")
    if result:
        return {"success": True, "trade_id": trade_id, "close_price": current_price}
    return {"success": False, "message": "Close failed"}


# ══════════════════════════════════════════════════════════════════════
# Learning & Discovery API
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/learning/stats")
async def get_learning_stats(days: int = Query(30, ge=1, le=365)):
    if not _pump_monitor:
        return {}
    return _pump_monitor.get_full_stats(days)


@app.get("/api/learning/false-positives")
async def get_false_positives(days: int = Query(30, ge=1, le=365)):
    if not _pump_monitor:
        return {"false_positives": [], "count": 0}
    fps = _pump_monitor.get_false_positives(days)
    return {"false_positives": fps, "count": len(fps)}


@app.get("/api/discovery/stats")
async def get_discovery_stats():
    if not _scanner:
        return {"enabled": False}
    return _scanner.get_discovery_stats()


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "whale-alert",
        "trader": _trader is not None,
        "scanner": _scanner is not None,
    }


# ══════════════════════════════════════════════════════════════════════
# Dashboard HTML
# ══════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    return _DASHBOARD_HTML


def start_dashboard():
    import uvicorn
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    start_dashboard()


# ══════════════════════════════════════════════════════════════════════
# HTML Template
# ══════════════════════════════════════════════════════════════════════

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Whale Alert Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#06080f;color:#d4daf0;font-family:'SF Mono','Fira Code','Consolas',monospace;padding:16px;font-size:13px}
a{color:#ff6b35;text-decoration:none}
h1{font-size:18px;margin-bottom:12px;display:flex;align-items:center;gap:8px}
h1 .dot{width:8px;height:8px;border-radius:50%;background:#00d68f;display:inline-block;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* Tabs */
.tabs{display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid #1a2240;padding-bottom:0}
.tab{padding:8px 18px;cursor:pointer;color:#5a6a8a;border-bottom:2px solid transparent;font-size:12px;font-weight:600;transition:all .2s}
.tab:hover{color:#d4daf0}
.tab.active{color:#ff6b35;border-bottom-color:#ff6b35}
.tab-content{display:none}
.tab-content.active{display:block}

/* Stats Cards */
.stats-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:16px}
.stat-card{background:#0c1120;border:1px solid #1a2240;border-radius:8px;padding:12px}
.stat-card .label{font-size:10px;color:#5a6a8a;text-transform:uppercase;letter-spacing:1px}
.stat-card .value{font-size:22px;font-weight:800;margin-top:4px}
.stat-card .sub{font-size:10px;color:#5a6a8a;margin-top:2px}
.green{color:#00d68f}.red{color:#ff4757}.orange{color:#ff9f43}.blue{color:#4a9eff}.yellow{color:#eab308}

/* Tables */
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;padding:8px 10px;color:#5a6a8a;font-size:10px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #1a2240}
td{padding:8px 10px;border-bottom:1px solid #0c1120}
tr:hover{background:#0c1120}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
.pill.long{background:#00d68f15;color:#00d68f}.pill.short{background:#ff475715;color:#ff4757}
.pill.open{background:#4a9eff15;color:#4a9eff}.pill.closed{background:#5a6a8a15;color:#5a6a8a}
.pill.manual{background:#eab30815;color:#eab308}.pill.tp{background:#00d68f15;color:#00d68f}.pill.sl{background:#ff475715;color:#ff4757}

/* Token Cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px}
.card{background:#0c1120;border:1px solid #1a2240;border-radius:8px;padding:12px;transition:border-color .2s}
.card.critical{border-color:#ff2d5540}.card.high{border-color:#ff9f4330}
.card .hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.card .sym{font-weight:800;font-size:14px}
.card .sc{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:700}
.sc.critical{background:#ff2d5520;color:#ff2d55}.sc.high{background:#ff9f4320;color:#ff9f43}
.sc.medium{background:#eab30820;color:#eab308}.sc.low{background:#64748b20;color:#64748b}
.card .meta{font-size:10px;color:#5a6a8a;line-height:1.7}
.sig{display:inline-block;font-size:9px;padding:1px 5px;border-radius:3px;margin:1px;background:#ff2d5510;color:#ff6b6b}

/* Bar */
.dim-bar{display:flex;gap:1px;margin-top:4px}
.dim-bar .seg{height:3px;border-radius:1px}

/* Footer */
#status{font-size:10px;color:#5a6a8a;margin-bottom:12px;display:flex;justify-content:space-between}
.btn{background:#ff6b3518;border:1px solid #ff6b3530;color:#ff6b35;border-radius:6px;padding:4px 12px;cursor:pointer;font-family:inherit;font-size:11px}
.btn:hover{background:#ff6b3530}
.btn-sm{padding:2px 8px;font-size:10px}
.btn-danger{background:#ff475718;border-color:#ff475730;color:#ff4757}
.empty{text-align:center;color:#5a6a8a;padding:40px;font-size:12px}
</style>
</head>
<body>

<h1><span class="dot"></span> WHALE ALERT DASHBOARD</h1>
<div id="status"><span id="status-text">Loading...</span><button class="btn" onclick="loadAll()">Refresh</button></div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('overview')">Overview</div>
  <div class="tab" onclick="switchTab('positions')">Positions</div>
  <div class="tab" onclick="switchTab('tokens')">Tokens</div>
  <div class="tab" onclick="switchTab('stats')">Stats</div>
</div>

<!-- Tab 1: Overview -->
<div id="tab-overview" class="tab-content active">
  <div class="stats-row" id="overview-stats"></div>
  <h3 style="font-size:13px;margin-bottom:8px;color:#5a6a8a">TOP TOKENS BY SCORE</h3>
  <table id="top-tokens-table"><thead><tr><th>Symbol</th><th>Price</th><th>24h</th><th>Score</th><th>Phase</th><th>Pump%</th></tr></thead><tbody></tbody></table>
</div>

<!-- Tab 2: Positions -->
<div id="tab-positions" class="tab-content">
  <h3 style="font-size:13px;margin-bottom:8px;color:#5a6a8a">OPEN POSITIONS</h3>
  <table id="open-trades-table"><thead><tr><th>#</th><th>Symbol</th><th>Tier</th><th>Dir</th><th>Entry</th><th>Current</th><th>PnL%</th><th>PnL$</th><th>SL/TP</th><th>Trailing</th><th>Action</th></tr></thead><tbody></tbody></table>
  <h3 style="font-size:13px;margin:16px 0 8px;color:#5a6a8a">TRADE HISTORY</h3>
  <table id="closed-trades-table"><thead><tr><th>#</th><th>Symbol</th><th>Tier</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL%</th><th>PnL$</th><th>Reason</th><th>Duration</th></tr></thead><tbody></tbody></table>
</div>

<!-- Tab 3: Tokens -->
<div id="tab-tokens" class="tab-content">
  <div class="cards" id="token-cards"></div>
</div>

<!-- Tab 4: Stats -->
<div id="tab-stats" class="tab-content">
  <div class="stats-row" id="trade-stats"></div>
  <h3 style="font-size:13px;margin:16px 0 8px;color:#5a6a8a">PUMP DETECTION STATS</h3>
  <div class="stats-row" id="pump-stats"></div>
  <h3 style="font-size:13px;margin:16px 0 8px;color:#5a6a8a">RECENT ALERTS</h3>
  <table id="alerts-table"><thead><tr><th>Time</th><th>Symbol</th><th>Score</th><th>Phase</th><th>Pump%</th></tr></thead><tbody></tbody></table>
</div>

<script>
const API = '';
let currentTab = 'overview';

function switchTab(name) {
  currentTab = name;
  document.querySelectorAll('.tab').forEach((t,i) => {
    const names = ['overview','positions','tokens','stats'];
    t.classList.toggle('active', names[i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
}

function fmt$(v, decimals) {
  if (!v || isNaN(v)) return '$0';
  decimals = decimals || (Math.abs(v) >= 1 ? 2 : Math.abs(v) >= 0.01 ? 4 : 6);
  return '$' + Number(v).toFixed(decimals);
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return '0%';
  const cls = v >= 0 ? 'green' : 'red';
  return `<span class="${cls}">${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%</span>`;
}

function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(typeof ts === 'number' && ts > 1e12 ? ts : ts * 1000);
  return d.toLocaleString('zh', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}

function fmtDuration(hours) {
  if (!hours) return '-';
  if (hours < 1) return Math.round(hours * 60) + 'm';
  if (hours < 24) return hours.toFixed(1) + 'h';
  return (hours / 24).toFixed(1) + 'd';
}

function scoreClass(s) {
  if (s >= 70) return 'critical';
  if (s >= 50) return 'high';
  if (s >= 30) return 'medium';
  return 'low';
}

function phaseColor(p) {
  const m = {'吸筹末期':'#ff2d55','即将拉盘':'#ff2d55','高度控盘':'#ff9f43','疑似吸筹':'#eab308','中度控盘':'#eab308','轻度异常':'#4a9eff'};
  return m[p] || '#5a6a8a';
}

async function loadOverview(tokens) {
  const statsEl = document.getElementById('overview-stats');
  const topEl = document.querySelector('#top-tokens-table tbody');

  // Fetch trade stats, open positions, & alerts in parallel
  const [tradeRes, openRes, alertRes] = await Promise.all([
    fetch(API + '/api/trades/stats').then(r => r.json()).catch(() => ({})),
    fetch(API + '/api/trades/open').then(r => r.json()).catch(() => ({count:0})),
    fetch(API + '/api/alerts?hours=24').then(r => r.json()).catch(() => ({count:0}))
  ]);

  const wr = tradeRes.win_rate || 0;
  const pnl = tradeRes.total_pnl_usd || 0;
  const openCount = openRes.count || 0;

  statsEl.innerHTML = `
    <div class="stat-card"><div class="label">Tokens Monitored</div><div class="value blue">${tokens.length}</div></div>
    <div class="stat-card"><div class="label">Open Positions</div><div class="value orange">${openCount}</div></div>
    <div class="stat-card"><div class="label">Win Rate</div><div class="value ${wr >= 50 ? 'green' : wr > 0 ? 'red' : 'blue'}">${wr ? wr.toFixed(1) + '%' : 'N/A'}</div></div>
    <div class="stat-card"><div class="label">Total PnL</div><div class="value ${pnl >= 0 ? 'green' : 'red'}">${pnl ? '$' + pnl.toFixed(2) : '$0'}</div></div>
    <div class="stat-card"><div class="label">Alerts (24h)</div><div class="value yellow">${alertRes.count || 0}</div></div>
    <div class="stat-card"><div class="label">Avg PnL/Trade</div><div class="value ${(tradeRes.avg_pnl_pct||0) >= 0 ? 'green' : 'red'}">${tradeRes.avg_pnl_pct ? tradeRes.avg_pnl_pct.toFixed(2) + '%' : 'N/A'}</div></div>
  `;

  // Top 10 tokens table
  const top10 = tokens.slice(0, 10);
  topEl.innerHTML = top10.map(t => `
    <tr>
      <td style="font-weight:700">${t.symbol}</td>
      <td>${fmt$(t.price)}</td>
      <td>${fmtPct(t.change_24h)}</td>
      <td><span class="sc ${scoreClass(t.control_score)}">${t.control_score}</span></td>
      <td style="color:${phaseColor(t.phase)}">${t.phase || '-'}</td>
      <td>${t.pump_probability || 0}%</td>
    </tr>
  `).join('');
}

async function loadPositions() {
  const [openRes, closedRes] = await Promise.all([
    fetch(API + '/api/trades/open').then(r => r.json()).catch(() => ({trades:[]})),
    fetch(API + '/api/trades/closed').then(r => r.json()).catch(() => ({trades:[]}))
  ]);

  // Fetch latest prices for open trades
  const tokensRes = await fetch(API + '/api/tokens').then(r => r.json()).catch(() => ({tokens:[]}));
  const priceMap = {};
  (tokensRes.tokens || []).forEach(t => priceMap[t.symbol] = t.price);

  const openTbody = document.querySelector('#open-trades-table tbody');
  const openTrades = openRes.trades || [];
  if (openTrades.length === 0) {
    openTbody.innerHTML = '<tr><td colspan="11" class="empty">No open positions</td></tr>';
  } else {
    openTbody.innerHTML = openTrades.map(t => {
      const cur = priceMap[t.symbol] || 0;
      const entry = t.entry_price || 0;
      let pnlPct = 0;
      if (entry > 0 && cur > 0) {
        pnlPct = t.direction === 'long'
          ? ((cur - entry) / entry * 100)
          : ((entry - cur) / entry * 100);
      }
      const pnlUsd = entry > 0 ? (t.position_size_usd || 200) * pnlPct / 100 : 0;
      const tier = t.signal_tier || 'medium';
      const tierCls = tier === 'strong' ? 'critical' : tier === 'medium' ? 'high' : 'low';
      const trailing = t.trailing_activated ? `Active` : '-';
      return `<tr>
        <td>#${t.id}</td>
        <td style="font-weight:700">${t.symbol}</td>
        <td><span class="sc ${tierCls}" style="font-size:10px">${tier.charAt(0).toUpperCase()}</span></td>
        <td><span class="pill ${t.direction}">${t.direction.toUpperCase()}</span></td>
        <td>${fmt$(entry)}</td>
        <td>${cur > 0 ? fmt$(cur) : '-'}</td>
        <td>${entry > 0 ? fmtPct(pnlPct) : '-'}</td>
        <td class="${pnlUsd >= 0 ? 'green' : 'red'}">${pnlUsd ? '$' + pnlUsd.toFixed(2) : '-'}</td>
        <td style="font-size:10px">${t.stop_loss_pct||'?'}%/${t.take_profit_pct||'?'}%</td>
        <td style="font-size:10px;${t.trailing_activated ? 'color:#00d68f' : ''}">${trailing}</td>
        <td><button class="btn btn-sm btn-danger" onclick="closeTrade(${t.id},'${t.symbol}')">Close</button></td>
      </tr>`;
    }).join('');
  }

  const closedTbody = document.querySelector('#closed-trades-table tbody');
  const closedTrades = closedRes.trades || [];
  if (closedTrades.length === 0) {
    closedTbody.innerHTML = '<tr><td colspan="10" class="empty">No closed trades yet</td></tr>';
  } else {
    closedTbody.innerHTML = closedTrades.map(t => {
      const dur = t.entry_time && t.exit_time ? (t.exit_time - t.entry_time) / 3600000 : 0;
      const tier = t.signal_tier || 'medium';
      const tierCls = tier === 'strong' ? 'critical' : tier === 'medium' ? 'high' : 'low';
      return `<tr>
        <td>#${t.id}</td>
        <td>${t.symbol}</td>
        <td><span class="sc ${tierCls}" style="font-size:10px">${tier.charAt(0).toUpperCase()}</span></td>
        <td><span class="pill ${t.direction}">${t.direction.toUpperCase()}</span></td>
        <td>${fmt$(t.entry_price)}</td>
        <td>${fmt$(t.exit_price)}</td>
        <td>${fmtPct(t.pnl_pct)}</td>
        <td class="${(t.pnl_usd||0) >= 0 ? 'green' : 'red'}">${t.pnl_usd ? '$' + t.pnl_usd.toFixed(2) : '-'}</td>
        <td><span class="pill ${t.close_reason}">${t.close_reason || '-'}</span></td>
        <td>${fmtDuration(dur)}</td>
      </tr>`;
    }).join('');
  }
}

async function loadTokenCards(tokens) {
  const el = document.getElementById('token-cards');
  el.innerHTML = tokens.map(t => {
    const cls = t.control_score >= 70 ? 'critical' : t.control_score >= 50 ? 'high' : '';
    const sCls = scoreClass(t.control_score);
    const chg = fmtPct(t.change_24h);
    const sigs = (t.signals || []).slice(0, 4).map(s => `<span class="sig">+${s.score} ${s.dimension}</span>`).join('');

    // Dimension bars
    const m = t.metrics || {};
    const dims = [
      {n:'Vol',v:m.accumulation||0,mx:20,c:'#ff6b35'},
      {n:'LO',v:m.large_orders||0,mx:18,c:'#eab308'},
      {n:'Imb',v:m.imbalance||0,mx:15,c:'#00d68f'},
      {n:'On',v:m.onchain_flow||0,mx:15,c:'#4a9eff'},
      {n:'Wsh',v:m.wash_trade||0,mx:12,c:'#ff4757'},
      {n:'Con',v:m.concentration||0,mx:12,c:'#a855f7'},
      {n:'Spr',v:m.spread||0,mx:8,c:'#64748b'},
    ];
    const bars = dims.map(d => {
      const w = Math.min(100, (d.v / d.mx) * 100);
      return `<span title="${d.n}:${d.v}/${d.mx}" style="display:inline-block;width:${Math.max(2,w/3)}%;height:3px;background:${d.c};border-radius:1px;opacity:.7"></span>`;
    }).join('');

    return `<div class="card ${cls}">
      <div class="hdr">
        <span class="sym">${t.symbol}</span>
        <span class="sc ${sCls}">${t.control_score}</span>
      </div>
      <div class="meta">
        ${fmt$(t.price)} ${chg} | ${t.phase ? t.phase : '-'} | Pump ${t.pump_probability || 0}%
      </div>
      <div style="display:flex;gap:1px;margin-top:4px">${bars}</div>
      <div style="margin-top:4px">${sigs}</div>
    </div>`;
  }).join('');
}

async function loadStats() {
  const [tradeRes, pumpRes, alertRes] = await Promise.all([
    fetch(API + '/api/trades/stats').then(r => r.json()).catch(() => ({})),
    fetch(API + '/api/learning/stats').then(r => r.json()).catch(() => ({})),
    fetch(API + '/api/alerts?hours=72').then(r => r.json()).catch(() => ({alerts:[]}))
  ]);

  // Trade stats cards
  const tsEl = document.getElementById('trade-stats');
  tsEl.innerHTML = `
    <div class="stat-card"><div class="label">Total Trades</div><div class="value">${tradeRes.total_trades || 0}</div></div>
    <div class="stat-card"><div class="label">Winning</div><div class="value green">${tradeRes.winning_trades || 0}</div></div>
    <div class="stat-card"><div class="label">Losing</div><div class="value red">${tradeRes.losing_trades || 0}</div></div>
    <div class="stat-card"><div class="label">Avg Win</div><div class="value green">${tradeRes.avg_win_pct ? '+' + tradeRes.avg_win_pct.toFixed(2) + '%' : 'N/A'}</div></div>
    <div class="stat-card"><div class="label">Avg Loss</div><div class="value red">${tradeRes.avg_loss_pct ? tradeRes.avg_loss_pct.toFixed(2) + '%' : 'N/A'}</div></div>
    <div class="stat-card"><div class="label">Sharpe Ratio</div><div class="value blue">${tradeRes.sharpe_ratio ? tradeRes.sharpe_ratio.toFixed(2) : 'N/A'}</div></div>
    <div class="stat-card"><div class="label">Avg Hold</div><div class="value">${tradeRes.avg_hold_hours ? fmtDuration(tradeRes.avg_hold_hours) : 'N/A'}</div></div>
  `;
  // Tier breakdown (if available)
  if (tradeRes.by_tier) {
    const tiers = tradeRes.by_tier;
    tsEl.innerHTML += [
      ['Strong', tiers.strong, 'critical'], ['Medium', tiers.medium, 'high'], ['Weak', tiers.weak, 'low']
    ].map(([name, t, cls]) =>
      `<div class="stat-card"><div class="label">${name} Tier</div><div class="value" style="font-size:16px"><span class="sc ${cls}">${t ? t.trades : 0}</span> ${t && t.trades ? t.win_rate.toFixed(0) + '%WR' : '-'}</div></div>`
    ).join('');
  }
  // Close reasons (if available)
  if (tradeRes.by_close_reason && Object.keys(tradeRes.by_close_reason).length > 0) {
    tsEl.innerHTML += `<div class="stat-card" style="grid-column:span 2"><div class="label">Exit Reasons</div><div class="value" style="font-size:12px">${Object.entries(tradeRes.by_close_reason).map(([r,c])=>`${r}:${c}`).join(' | ')}</div></div>`;
  }

  // Pump stats cards
  const psEl = document.getElementById('pump-stats');
  psEl.innerHTML = `
    <div class="stat-card"><div class="label">Pump Events (30d)</div><div class="value">${pumpRes.total_pumps || 0}</div></div>
    <div class="stat-card"><div class="label">Predicted</div><div class="value green">${pumpRes.predicted || 0}</div></div>
    <div class="stat-card"><div class="label">Missed</div><div class="value red">${pumpRes.missed || 0}</div></div>
    <div class="stat-card"><div class="label">Hit Rate</div><div class="value">${(pumpRes.hit_rate || 0).toFixed(1)}%</div></div>
    <div class="stat-card"><div class="label">Precision</div><div class="value">${(pumpRes.precision || 0).toFixed(1)}%</div></div>
    <div class="stat-card"><div class="label">Recall</div><div class="value">${(pumpRes.recall || 0).toFixed(1)}%</div></div>
    <div class="stat-card"><div class="label">False Positives</div><div class="value red">${pumpRes.false_positives || 0}</div></div>
  `;

  // Alerts table
  const alertsTbody = document.querySelector('#alerts-table tbody');
  const alerts = alertRes.alerts || [];
  if (alerts.length === 0) {
    alertsTbody.innerHTML = '<tr><td colspan="5" class="empty">No alerts in the last 72 hours</td></tr>';
  } else {
    alertsTbody.innerHTML = alerts.slice(0, 20).map(a => `
      <tr>
        <td>${fmtTime(a.timestamp || a.created_at)}</td>
        <td style="font-weight:700">${a.symbol}</td>
        <td><span class="sc ${scoreClass(a.control_score)}" style="font-size:10px">${a.control_score}</span></td>
        <td style="color:${phaseColor(a.phase)}">${a.phase || '-'}</td>
        <td>${a.pump_probability || 0}%</td>
      </tr>
    `).join('');
  }
}

async function closeTrade(id, symbol) {
  if (!confirm(`Close position #${id} ${symbol}?`)) return;
  try {
    const res = await fetch(API + '/api/trades/close/' + id, {method: 'POST'});
    const data = await res.json();
    if (data.success) {
      alert(`Closed #${id} at ${fmt$(data.close_price)}`);
      loadAll();
    } else {
      alert('Close failed: ' + data.message);
    }
  } catch(e) { alert('Error: ' + e.message); }
}

async function loadAll() {
  try {
    const tokensRes = await fetch(API + '/api/tokens');
    const tokensData = await tokensRes.json();
    const tokens = (tokensData.tokens || []).sort((a, b) => b.control_score - a.control_score);

    document.getElementById('status-text').textContent =
      `${tokens.length} tokens | ${new Date().toLocaleTimeString()}`;

    loadOverview(tokens);
    loadTokenCards(tokens);

    // Only load heavy data when tab is active
    if (currentTab === 'positions') loadPositions();
    if (currentTab === 'stats') loadStats();
  } catch(e) {
    document.getElementById('status-text').textContent = 'Error: ' + e.message;
  }
}

// Lazy load tabs on switch
const origSwitch = switchTab;
switchTab = function(name) {
  origSwitch(name);
  if (name === 'positions') loadPositions();
  if (name === 'stats') loadStats();
};

loadAll();
setInterval(loadAll, 60000);
</script>
</body>
</html>"""
