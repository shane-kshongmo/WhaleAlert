"""
FastAPI 仪表盘 API
提供 REST 接口查看所有代币控盘状态和预警历史
"""
import json
import logging
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from data.data_store import DataStore
from config import WEB_HOST, WEB_PORT

logger = logging.getLogger(__name__)

app = FastAPI(title="Whale Alert Dashboard", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

store = DataStore()

# 延迟初始化, 供外部注入
_token_manager = None
_pump_monitor = None
_scanner = None

def set_managers(token_mgr, pump_mon, scanner=None):
    global _token_manager, _pump_monitor, _scanner
    _token_manager = token_mgr
    _pump_monitor = pump_mon
    _scanner = scanner


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
    """获取当前所有高分代币 (控盘分>=70)"""
    snapshots = store.get_all_latest()
    critical = [s for s in snapshots if s.get("control_score", 0) >= 70]
    return {"critical_tokens": critical, "count": len(critical)}


# ═══ 代币管理 API ═══

@app.post("/api/tokens/add")
async def add_token(symbol: str, name: str = "", chain: str = "eth", contract: str = ""):
    """添加新代币到监控列表"""
    if not _token_manager:
        return {"success": False, "message": "Token manager not initialized"}
    return _token_manager.add_token(symbol, name, chain, contract)

@app.post("/api/tokens/remove")
async def remove_token(symbol: str):
    """从监控列表移除代币"""
    if not _token_manager:
        return {"success": False, "message": "Token manager not initialized"}
    return _token_manager.remove_token(symbol)

@app.get("/api/tokens/list")
async def list_watched_tokens():
    """获取当前完整监控列表"""
    if not _token_manager:
        return {"tokens": [], "count": 0}
    tokens = _token_manager.list_tokens()
    return {"tokens": tokens, "count": len(tokens)}

@app.post("/api/tokens/batch")
async def add_tokens_batch(tokens: list):
    """批量添加代币"""
    if not _token_manager:
        return {"success": False, "message": "Token manager not initialized"}
    return _token_manager.add_tokens_batch(tokens)


# ═══ 学习统计 API ═══

@app.get("/api/learning/stats")
async def get_learning_stats(days: int = Query(30, ge=1, le=365)):
    """获取完整学习统计: 命中/漏报/误报"""
    if not _pump_monitor:
        return {}
    return _pump_monitor.get_full_stats(days)

@app.get("/api/learning/false-positives")
async def get_false_positives(days: int = Query(30, ge=1, le=365)):
    """获取误报记录"""
    if not _pump_monitor:
        return {"false_positives": [], "count": 0}
    fps = _pump_monitor.get_false_positives(days)
    return {"false_positives": fps, "count": len(fps)}


@app.get("/api/discovery/stats")
async def get_discovery_stats():
    """获取自动发现扫描统计"""
    if not _scanner:
        return {"enabled": False}
    return _scanner.get_discovery_stats()


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "whale-alert"}


@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    """简易 HTML 仪表盘"""
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🐋 Whale Alert Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #06080f; color: #d4daf0; font-family: 'SF Mono', 'Fira Code', monospace; padding: 20px; }
h1 { font-size: 20px; margin-bottom: 16px; }
h1 span { color: #ff6b35; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 12px; }
.card { background: #0c1120; border: 1px solid #1a2240; border-radius: 10px; padding: 14px; transition: border-color 0.2s; }
.card.critical { border-color: #ff2d5540; }
.card.high { border-color: #ff9f4330; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.symbol { font-weight: 800; font-size: 16px; }
.score { display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 700; }
.score.critical { background: #ff2d5520; color: #ff2d55; }
.score.high { background: #ff9f4320; color: #ff9f43; }
.score.medium { background: #eab30820; color: #eab308; }
.score.low { background: #64748b20; color: #64748b; }
.phase { font-size: 11px; padding: 2px 8px; border-radius: 12px; }
.metrics { font-size: 11px; color: #5a6a8a; line-height: 1.8; }
.signal-tag { display: inline-block; font-size: 9px; padding: 2px 6px; border-radius: 4px; margin: 2px; background: #ff2d5515; color: #ff6b6b; }
.up { color: #00d68f; } .down { color: #ff4757; }
.refresh { background: #ff6b3520; border: 1px solid #ff6b3540; color: #ff6b35; border-radius: 8px; padding: 6px 16px; cursor: pointer; font-family: inherit; font-size: 12px; }
.bar { display: flex; gap: 2px; margin-top: 6px; }
.bar-seg { height: 4px; border-radius: 2px; }
#status { font-size: 10px; color: #5a6a8a; margin-bottom: 12px; }
</style>
</head>
<body>
<h1>🐋 <span>WHALE ALERT</span> DASHBOARD</h1>
<div id="status">加载中...</div>
<div class="cards" id="cards"></div>

<script>
async function load() {
  try {
    const resp = await fetch('/api/tokens');
    const data = await resp.json();
    const el = document.getElementById('cards');
    document.getElementById('status').textContent =
      `${data.count} 个代币 | ${new Date().toLocaleTimeString()} 更新`;

    el.innerHTML = data.tokens
      .sort((a, b) => b.control_score - a.control_score)
      .map(t => {
        const cls = t.control_score >= 70 ? 'critical' : t.control_score >= 50 ? 'high' : '';
        const scoreCls = t.control_score >= 70 ? 'critical' : t.control_score >= 50 ? 'high' : t.control_score >= 30 ? 'medium' : 'low';
        const chg = t.change_24h >= 0 ? `<span class="up">+${t.change_24h.toFixed(2)}%</span>` : `<span class="down">${t.change_24h.toFixed(2)}%</span>`;
        const signals = (t.signals || []).map(s => `<span class="signal-tag">+${s.score} ${s.dimension}</span>`).join('');
        return `<div class="card ${cls}">
          <div class="header">
            <span class="symbol">${t.symbol}</span>
            <span class="score ${scoreCls}">${t.control_score} 控盘分</span>
          </div>
          <div class="metrics">
            💰 $${Number(t.price).toPrecision(5)} ${chg}<br>
            ${t.phase ? `📍 ${t.phase}` : ''} ${t.pump_probability ? `| 🔥 拉盘率 ${t.pump_probability}%` : ''}
          </div>
          <div style="margin-top:6px">${signals}</div>
        </div>`;
      }).join('');
  } catch(e) {
    document.getElementById('status').textContent = '加载失败: ' + e.message;
  }
}
load();
setInterval(load, 60000);
</script>
</body>
</html>"""


def start_dashboard():
    import uvicorn
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    start_dashboard()
