"""Single-origin multi-page live dashboard.

All pages are served from the same aiohttp app on one port. Client-side
hash routing switches between pages without a full reload. The server
exposes one data endpoint per page so each page only fetches what it needs.

Routes
------
    GET  /                         → shell + client-side router
    GET  /api/overview             → scalar metrics (equity, PnL, DD, …)
    GET  /api/positions            → per-coin position, regime, confidence
    GET  /api/orders               → order counters + recent order log
    GET  /api/system               → kill switch state, infra metrics
    GET  /api/charts               → latest candle data per coin
    GET  /api/logs                 → last N structured log lines
    POST /api/kill-switch/trigger  → manually trigger emergency stop
    POST /api/kill-switch/reset    → reset kill switch

Usage
-----
    # Embedded in the main trader process:
    from autotrader.monitoring.web import start_dashboard, set_kill_switch
    set_kill_switch(kill_switch_instance)
    start_dashboard(port=8080)

    # Standalone:
    uv run python -m autotrader.monitoring.web --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import threading
from typing import Any, Optional

from aiohttp import web
from prometheus_client import REGISTRY

# ---------------------------------------------------------------------------
# In-process log buffer
# ---------------------------------------------------------------------------

_LOG_BUFFER: collections.deque[dict[str, Any]] = collections.deque(maxlen=500)


def push_log(record: dict[str, Any]) -> None:
    """Append a structured log record to the in-process buffer."""
    _LOG_BUFFER.appendleft(record)


# ---------------------------------------------------------------------------
# In-process coin chart data buffer
# ---------------------------------------------------------------------------

_COIN_CHART_DATA: dict[str, dict[str, Any]] = {}
_CHART_MAX_BARS = 150


def update_coin_data(coin: str, interval: str, df: Any) -> None:
    """Push the latest candles for *coin* into the chart buffer.

    Called by the trading scheduler after each candle read so the dashboard
    always shows fresh data without hitting the datastore directly.
    """
    try:
        tail = df.tail(_CHART_MAX_BARS)
        _COIN_CHART_DATA[coin] = {
            "interval": interval,
            "t": tail["t"].tolist(),
            "o": tail["o"].round(6).tolist(),
            "h": tail["h"].round(6).tolist(),
            "l": tail["l"].round(6).tolist(),
            "c": tail["c"].round(6).tolist(),
            "v": tail["v"].round(2).tolist(),
        }
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Live kill switch reference (wired in by main.py)
# ---------------------------------------------------------------------------

_kill_switch_ref: Optional[Any] = None  # KillSwitch instance


def set_kill_switch(ks: Any) -> None:
    """Wire the live KillSwitch instance so the dashboard can control it."""
    global _kill_switch_ref
    _kill_switch_ref = ks


# ---------------------------------------------------------------------------
# Prometheus helpers
# ---------------------------------------------------------------------------

def _gauge(name: str, labels: dict[str, str] | None = None) -> float | None:
    for metric in REGISTRY.collect():
        if metric.name == name:
            samples = [
                s for s in metric.samples
                if s.name in (name, name + "_total")
                and (labels is None or all(s.labels.get(k) == v for k, v in labels.items()))
            ]
            if samples:
                return samples[0].value
    return None


def _counter_sum(name: str) -> float:
    total = 0.0
    for metric in REGISTRY.collect():
        if metric.name == name:
            for s in metric.samples:
                if s.name.endswith("_total"):
                    total += s.value
    return total


def _histogram_quantile(name: str, q: float) -> float | None:
    for metric in REGISTRY.collect():
        if metric.name == name:
            buckets: list[tuple[float, float]] = []
            count = 0.0
            for s in metric.samples:
                if s.name == name + "_bucket":
                    buckets.append((float(s.labels.get("le", "inf")), s.value))
                elif s.name == name + "_count":
                    count = s.value
            if not buckets or count == 0:
                return None
            target = q * count
            prev_le, prev_c = 0.0, 0.0
            for le, c in buckets:
                if c >= target:
                    if c == prev_c:
                        return prev_le
                    return prev_le + (target - prev_c) / (c - prev_c) * (le - prev_le)
                prev_le, prev_c = le, c
    return None


def _histogram_p50(name: str) -> float | None:
    return _histogram_quantile(name, 0.5)


def _per_coin(metric_name: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for metric in REGISTRY.collect():
        if metric.name == metric_name:
            for s in metric.samples:
                coin = s.labels.get("coin", "")
                if coin:
                    out[coin] = s.value
    return out


def _active_regime_per_coin() -> dict[str, str]:
    out: dict[str, str] = {}
    for metric in REGISTRY.collect():
        if metric.name == "current_regime":
            for s in metric.samples:
                if s.value == 1.0:
                    coin = s.labels.get("coin", "")
                    regime = s.labels.get("regime", "")
                    if coin:
                        out[coin] = regime
    return out


def _orders_by_label() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in REGISTRY.collect():
        if metric.name in ("orders_placed", "orders_filled", "orders_cancelled"):
            for s in metric.samples:
                if s.name.endswith("_total") and s.value > 0:
                    rows.append({
                        "metric": metric.name,
                        "labels": dict(s.labels),
                        "value": s.value,
                    })
    return rows


def _api_errors_by_label() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in REGISTRY.collect():
        if metric.name == "api_errors":
            for s in metric.samples:
                if s.name.endswith("_total") and s.value > 0:
                    rows.append({"labels": dict(s.labels), "value": s.value})
    return rows


def _slippage_buckets() -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    for metric in REGISTRY.collect():
        if metric.name == "fill_slippage_bps":
            coin_buckets: dict[str, list[tuple[str, float]]] = {}
            for s in metric.samples:
                if s.name == "fill_slippage_bps_bucket":
                    coin = s.labels.get("coin", "ALL")
                    coin_buckets.setdefault(coin, []).append(
                        (s.labels.get("le", "inf"), s.value)
                    )
            for coin, bs in coin_buckets.items():
                buckets.append({"coin": coin, "buckets": bs})
    return buckets


def _load_kill_switch() -> dict[str, Any]:
    """Read KS state from the live object or fall back to disk."""
    if _kill_switch_ref is not None:
        ev = _kill_switch_ref.get_event()
        return {
            "triggered": ev.triggered,
            "trigger": ev.trigger.value if ev.trigger else None,
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            "details": ev.details,
        }
    import os
    from pathlib import Path
    ks_file = Path(os.path.expanduser("~/.autotrader/kill_switch.json"))
    if ks_file.exists():
        try:
            return json.loads(ks_file.read_text())
        except Exception:
            pass
    return {"triggered": False, "trigger": None, "timestamp": None, "details": ""}


# ---------------------------------------------------------------------------
# API handlers
# ---------------------------------------------------------------------------

async def _api_overview(req: web.Request) -> web.Response:  # noqa: ARG001
    errors = _counter_sum("api_errors")
    ks = _load_kill_switch()
    return _json({
        "account_equity_usd": _gauge("account_equity_usd"),
        "daily_pnl_usd": _gauge("daily_pnl_usd"),
        "max_drawdown_pct": _gauge("max_drawdown_pct"),
        "active_positions": _gauge("active_positions"),
        "orders_placed_total": _counter_sum("orders_placed"),
        "orders_filled_total": _counter_sum("orders_filled"),
        "orders_cancelled_total": _counter_sum("orders_cancelled"),
        "api_errors_total": errors,
        "ws_reconnects_total": _counter_sum("ws_reconnects"),
        "loop_latency_p50_ms": (_histogram_p50("loop_latency_seconds") or 0) * 1000,
        "kill_switch_triggered": ks.get("triggered", False),
        "kill_switch_trigger": ks.get("trigger"),
    })


async def _api_positions(req: web.Request) -> web.Response:  # noqa: ARG001
    pnl = _per_coin("position_pnl_usd")
    confidence = _per_coin("signal_confidence")
    regimes = _active_regime_per_coin()
    coins = sorted(set(pnl) | set(confidence) | set(regimes))
    rows = []
    for coin in coins:
        rows.append({
            "coin": coin,
            "pnl_usd": pnl.get(coin),
            "regime": regimes.get(coin, "unknown"),
            "confidence": confidence.get(coin),
        })
    return _json({"positions": rows})


async def _api_orders(req: web.Request) -> web.Response:  # noqa: ARG001
    return _json({
        "by_label": _orders_by_label(),
        "slippage_buckets": _slippage_buckets(),
    })


async def _api_system(req: web.Request) -> web.Response:  # noqa: ARG001
    ks = _load_kill_switch()
    errors = _api_errors_by_label()
    total_errors = sum(e["value"] for e in errors)
    return _json({
        "kill_switch": ks,
        "kill_switch_controllable": _kill_switch_ref is not None,
        "ws_reconnects": _counter_sum("ws_reconnects"),
        "api_errors": errors,
        "api_errors_total": total_errors,
        "loop_latency_p50_ms": (_histogram_p50("loop_latency_seconds") or 0) * 1000,
        "loop_latency_p95_ms": (_histogram_quantile("loop_latency_seconds", 0.95) or 0) * 1000,
    })


async def _api_charts(req: web.Request) -> web.Response:  # noqa: ARG001
    regimes = _active_regime_per_coin()
    pnl = _per_coin("position_pnl_usd")
    result = {}
    for coin, data in _COIN_CHART_DATA.items():
        result[coin] = {
            **data,
            "regime": regimes.get(coin, "unknown"),
            "pnl_usd": pnl.get(coin),
        }
    return _json(result)


async def _api_logs(req: web.Request) -> web.Response:
    limit = int(req.rel_url.query.get("limit", "200"))
    return _json({"logs": list(_LOG_BUFFER)[:limit]})


async def _api_ks_trigger(req: web.Request) -> web.Response:
    if _kill_switch_ref is None:
        return web.Response(status=503, text="Kill switch not connected to dashboard")
    try:
        body: dict = {}
        try:
            body = await req.json()
        except Exception:
            pass
        reason = body.get("reason", "Manual trigger from dashboard")

        from autotrader.runtime.kill_switch import KillSwitchTrigger

        class _NullBroker:
            async def cancel_all(self) -> int:
                return 0

        await _kill_switch_ref.execute(
            broker=_NullBroker(),
            trigger=KillSwitchTrigger.MANUAL,
            details=reason,
        )
        return _json({"ok": True})
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})


async def _api_ks_reset(req: web.Request) -> web.Response:
    if _kill_switch_ref is None:
        return web.Response(status=503, text="Kill switch not connected to dashboard")
    try:
        body: dict = {}
        try:
            body = await req.json()
        except Exception:
            pass
        reason = body.get("reason", "Reset from dashboard")
        _kill_switch_ref.reset(reason=reason)
        return _json({"ok": True})
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})


async def _handle_index(req: web.Request) -> web.Response:  # noqa: ARG001
    return web.Response(text=_HTML, content_type="text/html")


def _json(data: Any) -> web.Response:
    return web.Response(
        text=json.dumps(data, default=str),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# HTML shell + client-side multi-page app
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoTrader</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#0d0f14;--surface:#161b22;--surface2:#1c2128;--border:#21262d;
  --text:#e6edf3;--muted:#7d8590;--green:#3fb950;--red:#f85149;
  --yellow:#d29922;--blue:#58a6ff;--accent:#1f6feb;--purple:#bc8cff;
  --nav-w:200px;--topbar-h:54px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{display:flex;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;height:100vh;overflow:hidden}

/* ── progress bar ── */
#progress-bar{position:fixed;top:0;left:0;right:0;height:2px;z-index:9999;background:transparent}
#progress-bar-fill{height:100%;width:0%;background:var(--blue);transition:width .1s linear}

/* ── nav ── */
nav{width:var(--nav-w);min-width:var(--nav-w);background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;padding:0;z-index:10}
.nav-logo{padding:16px 18px 14px;font-size:14px;font-weight:700;letter-spacing:.03em;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}
.nav-logo-icon{width:24px;height:24px;background:var(--accent);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0}
.nav-logo span{color:var(--blue)}
.nav-links{flex:1;padding:8px 0}
.nav-link{display:flex;align-items:center;gap:9px;padding:9px 18px;color:var(--muted);text-decoration:none;font-size:13px;cursor:pointer;transition:background .15s,color .15s,border-color .15s;border-left:3px solid transparent;position:relative}
.nav-link:hover{background:var(--surface2);color:var(--text)}
.nav-link.active{color:var(--blue);border-left-color:var(--blue);background:rgba(31,111,235,.1)}
.nav-link svg{width:15px;height:15px;flex-shrink:0;opacity:.8}
.nav-link span.nav-label{flex:1}
.kb{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:3px;border:1px solid var(--border);background:var(--surface2);font-size:9px;font-weight:700;color:var(--muted);letter-spacing:0;flex-shrink:0}
.nav-badge{background:var(--red);color:#fff;border-radius:8px;font-size:9px;font-weight:700;padding:1px 5px;min-width:16px;text-align:center;line-height:14px}
.nav-badge.warn{background:var(--yellow);color:#000}
.nav-ks-dot{width:7px;height:7px;border-radius:50%;background:var(--red);animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.nav-bottom{padding:12px 18px;border-top:1px solid var(--border);font-size:11px;color:var(--muted)}
#status-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-right:5px;vertical-align:middle;transition:background .3s}
#status-dot.dead{background:var(--red)}

/* ── layout ── */
.right{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}

/* ── topbar ── */
.topbar{height:var(--topbar-h);min-height:var(--topbar-h);background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 20px;gap:0;flex-shrink:0;overflow:hidden}
.tb-item{display:flex;align-items:center;gap:6px;padding:0 14px;border-right:1px solid var(--border);height:100%}
.tb-item:first-child{padding-left:0}
.tb-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;white-space:nowrap}
.tb-val{font-size:14px;font-weight:700;white-space:nowrap;transition:transform .15s ease}
.tb-val.pop{animation:numPop .3s ease}
@keyframes numPop{0%{transform:scale(1)}50%{transform:scale(1.06)}100%{transform:scale(1)}}
.tb-val.sm{font-size:12px}
.status-pill{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.04em}
.status-pill .dot{width:6px;height:6px;border-radius:50%}
.pill-running{background:rgba(63,185,80,.15);color:var(--green)}
.pill-running .dot{background:var(--green);animation:pulse 2s ease-in-out infinite}
.pill-halted{background:rgba(248,81,73,.15);color:var(--red)}
.pill-halted .dot{background:var(--red);animation:pulse 1s ease-in-out infinite}
.pill-nodata{background:var(--surface2);color:var(--muted)}
.pill-nodata .dot{background:var(--muted)}
.dd-bar-wrap{display:flex;flex-direction:column;gap:3px;width:110px}
.dd-bar-track{height:5px;background:var(--surface2);border-radius:3px;overflow:hidden}
.dd-bar-fill{height:100%;border-radius:3px;transition:width .4s,background .4s}
.dd-bar-label{font-size:10px;color:var(--muted);display:flex;justify-content:space-between}
.tb-ks-ok{color:var(--green);font-size:11px;font-weight:600;white-space:nowrap}
.tb-ks-alert{color:var(--red);font-size:11px;font-weight:700;animation:pulse 1s ease-in-out infinite;cursor:pointer;text-decoration:underline;white-space:nowrap}
#tb-ago{font-size:11px;color:var(--muted);white-space:nowrap;min-width:60px;text-align:right}
#tb-ago.fresh{color:var(--green)}
#tb-ago.stale{color:var(--yellow)}
.tb-sparkline{width:60px;height:28px;flex-shrink:0}

/* ── main ── */
main{flex:1;overflow-y:auto;padding:0;min-width:0}
.page{display:none;padding:24px}
.page.active{display:block}
h1{font-size:16px;font-weight:600;margin-bottom:18px;display:flex;align-items:center;gap:10px}

/* ── hero stats ── */
.hero-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-bottom:24px}
.hero-stat{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px 20px;position:relative;overflow:hidden;transition:border-color .2s,box-shadow .2s}
.hero-stat:hover{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.hero-stat::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.hero-stat.equity::before{background:var(--blue)}
.hero-stat.pnl-pos::before{background:var(--green)}
.hero-stat.pnl-neg::before{background:var(--red)}
.hero-stat.dd::before{background:var(--yellow)}
.hero-stat.pos-count::before{background:var(--purple)}
.hero-stat .lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px}
.hero-stat .big{font-size:28px;font-weight:800;line-height:1;transition:transform .15s ease}
.hero-stat .big.pop{animation:numPop .3s ease}
.hero-stat .sub{font-size:11px;color:var(--muted);margin-top:6px}

/* ── stat grid ── */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;margin-bottom:20px}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px;transition:border-color .2s,box-shadow .2s}
.stat:hover{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.stat .lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:5px}
.stat .val{font-size:22px;font-weight:700;transition:transform .15s ease}
.stat .val.pop{animation:numPop .3s ease}
.stat .sub{font-size:11px;color:var(--muted);margin-top:3px}
.pos{color:var(--green)}.neg{color:var(--red)}.neu{color:var(--text)}.warn{color:var(--yellow)}

/* ── chart-row ── */
.chart-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px}
.chart-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
.chart-card h2{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:12px}
canvas{max-height:150px}

/* ── tables ── */
.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:16px}
.table-hdr{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-bottom:1px solid var(--border)}
.table-hdr h2{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
table{width:100%;border-collapse:collapse}
th,td{padding:9px 16px;text-align:left;font-size:12px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500;background:var(--surface2)}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--surface2)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.badge-blue{background:rgba(88,166,255,.15);color:var(--blue)}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.badge-muted{background:var(--surface2);color:var(--muted)}

/* ── regime row borders ── */
tr.regime-trend_up td:first-child{border-left:3px solid var(--green)}
tr.regime-trend_down td:first-child{border-left:3px solid var(--red)}
tr.regime-range td:first-child{border-left:3px solid var(--blue)}
tr.regime-high_vol td:first-child{border-left:3px solid var(--yellow)}
tr.regime-low_vol td:first-child{border-left:3px solid var(--muted)}

/* ── empty ── */
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 20px;color:var(--muted);gap:10px}
.empty svg{opacity:.3}
.empty p{font-size:13px}

/* ── ks card ── */
.ks-card{border-radius:10px;padding:18px 20px;margin-bottom:18px;border:1px solid}
.ks-card.ok{background:rgba(63,185,80,.06);border-color:rgba(63,185,80,.25)}
.ks-card.triggered{background:rgba(248,81,73,.08);border-color:rgba(248,81,73,.4)}
.ks-card-header{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.ks-card-header svg{flex-shrink:0}
.ks-card-title{font-size:14px;font-weight:700}
.ks-card-detail{font-size:12px;color:var(--muted);margin-top:2px}
.ks-actions{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid;transition:all .15s;letter-spacing:.02em}
.btn-danger{background:rgba(248,81,73,.1);border-color:rgba(248,81,73,.4);color:var(--red)}
.btn-danger:hover{background:rgba(248,81,73,.2);border-color:var(--red)}
.btn-warn{background:rgba(210,153,34,.1);border-color:rgba(210,153,34,.4);color:var(--yellow)}
.btn-warn:hover{background:rgba(210,153,34,.2);border-color:var(--yellow)}
.btn-muted{background:var(--surface2);border-color:var(--border);color:var(--muted)}
.btn-muted:hover{color:var(--text);border-color:var(--muted)}
.btn:disabled{opacity:.4;cursor:not-allowed}

/* ── modal ── */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.modal h3{font-size:15px;font-weight:700;margin-bottom:10px}
.modal p{font-size:13px;color:var(--muted);margin-bottom:20px;line-height:1.6}
.modal-actions{display:flex;gap:10px;justify-content:flex-end}
.modal textarea{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:8px 10px;font-size:12px;resize:vertical;min-height:60px;font-family:inherit;margin-bottom:16px}

/* ── toast ── */
#toast-container{position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.toast{display:flex;align-items:flex-start;gap:10px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:11px 14px;min-width:260px;max-width:360px;box-shadow:0 4px 20px rgba(0,0,0,.4);pointer-events:all;animation:slideInRight .25s ease;border-left:3px solid var(--muted)}
.toast.success{border-left-color:var(--green)}
.toast.error{border-left-color:var(--red)}
.toast.warn{border-left-color:var(--yellow)}
.toast.info{border-left-color:var(--blue)}
.toast-msg{font-size:12px;line-height:1.5;flex:1;color:var(--text)}
.toast-close{font-size:14px;color:var(--muted);cursor:pointer;flex-shrink:0;line-height:1;margin-top:-1px}
.toast-close:hover{color:var(--text)}
@keyframes slideInRight{from{transform:translateX(30px);opacity:0}to{transform:translateX(0);opacity:1}}
@keyframes slideOutRight{from{transform:translateX(0);opacity:1}to{transform:translateX(30px);opacity:0}}
.toast.removing{animation:slideOutRight .2s ease forwards}

/* ── log stream ── */
.log-controls{display:flex;gap:8px;margin-bottom:12px;align-items:center;flex-wrap:wrap}
.log-controls input{background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:6px 10px;font-size:12px;flex:1;min-width:160px;outline:none;transition:border-color .15s}
.log-controls input:focus{border-color:var(--blue)}
.log-controls select{background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:6px 10px;font-size:12px;outline:none}
.log-toggle{background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--muted);padding:5px 12px;font-size:11px;font-weight:600;cursor:pointer;transition:all .15s}
.log-toggle.on{background:rgba(88,166,255,.1);border-color:var(--blue);color:var(--blue)}
#log-stream{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 0;font-family:"SF Mono",SFMono-Regular,Consolas,monospace;font-size:11px;height:calc(100vh - 220px);overflow-y:auto}
.log-line{padding:3px 14px;display:flex;gap:8px;line-height:1.5}
.log-line:hover{background:var(--surface2)}
.log-ts{color:var(--muted);flex-shrink:0;min-width:90px}
.log-lvl{flex-shrink:0;min-width:60px;font-weight:700}
.log-lvl.DEBUG{color:var(--muted)}.log-lvl.INFO{color:var(--blue)}.log-lvl.WARNING{color:var(--yellow)}.log-lvl.ERROR,.log-lvl.CRITICAL{color:var(--red)}
.log-event{color:var(--text);flex:1;word-break:break-all}.log-ctx{color:var(--muted)}
mark.hl{background:rgba(210,153,34,.3);color:var(--text);border-radius:2px;padding:0 1px}

/* ── charts page ── */
#page-charts{padding:0;display:none;flex-direction:column}
#page-charts.active{display:flex;height:calc(100vh - var(--topbar-h))}
.chart-page-hdr{display:flex;align-items:center;justify-content:space-between;padding:10px 18px;border-bottom:1px solid var(--border);flex-shrink:0;gap:12px}
.coin-tabs{display:flex;gap:4px;flex-wrap:wrap}
.coin-tab{padding:5px 14px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--muted);font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;letter-spacing:.02em}
.coin-tab:hover{border-color:var(--blue);color:var(--text)}
.coin-tab.active{background:var(--accent);border-color:var(--accent);color:#fff}
.chart-meta-strip{display:flex;align-items:center;gap:10px;flex-shrink:0}
.tv-container{flex:1;min-height:0;position:relative}
.tv-container>div,.tv-container iframe{position:absolute;inset:0;width:100%;height:100%}

/* ── responsive ── */
@media(max-width:768px){
  nav{width:52px;min-width:52px}
  .nav-logo-icon{display:none}.nav-logo span,.nav-logo b{display:none}
  .nav-link span.nav-label,.nav-badge,.kb{display:none}.nav-link{padding:12px;justify-content:center}
  .chart-row{grid-template-columns:1fr}
  .topbar{gap:0;padding:0 10px}
  .tb-item{padding:0 8px}
  .hero-grid{grid-template-columns:1fr 1fr}
  .chart-page-hdr{flex-direction:column;align-items:flex-start}
}
@media(max-width:480px){
  .topbar .tb-item:nth-child(n+4){display:none}
  .hero-grid{grid-template-columns:1fr}
}
</style>
</head>
<body>

<!-- progress bar -->
<div id="progress-bar"><div id="progress-bar-fill"></div></div>

<!-- toast container -->
<div id="toast-container"></div>

<!-- left nav -->
<nav>
  <div class="nav-logo">
    <div class="nav-logo-icon">&#9889;</div>
    <b>Auto</b><span>Trader</span>
  </div>
  <div class="nav-links">
    <a class="nav-link active" data-page="overview" onclick="go('overview')">
      <svg viewBox="0 0 16 16" fill="currentColor"><path d="M2 2h5v5H2zm7 0h5v5H9zM2 9h5v5H2zm7 0h5v5H9z"/></svg>
      <span class="nav-label">Overview</span>
      <span class="kb">1</span>
    </a>
    <a class="nav-link" data-page="positions" onclick="go('positions')">
      <svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 2.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm.5 1.5h-1v3.69l2.53 1.46.75-1.3-2.28-1.32V5z"/></svg>
      <span class="nav-label">Positions</span>
      <span class="kb">2</span>
    </a>
    <a class="nav-link" data-page="orders" onclick="go('orders')">
      <svg viewBox="0 0 16 16" fill="currentColor"><path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h11A1.5 1.5 0 0 1 15 2.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 1 13.5zm1.5 0v11h11v-11zM4 5h8v1.5H4zm0 3h8v1.5H4zm0 3h5v1.5H4z"/></svg>
      <span class="nav-label">Orders</span>
      <span class="kb">3</span>
    </a>
    <a class="nav-link" data-page="system" onclick="go('system')">
      <svg viewBox="0 0 16 16" fill="currentColor"><path d="M6.5 1h3l.5 1.5c.4.1.78.27 1.13.48l1.43-.52 2.12 2.12-.52 1.43c.21.35.37.73.48 1.13L16 7.5v3l-1.5.5c-.11.4-.27.78-.48 1.13l.52 1.43-2.12 2.12-1.43-.52c-.35.21-.73.37-1.13.48L9.5 17h-3l-.5-1.5a5.06 5.06 0 0 1-1.13-.48l-1.43.52L1.32 13.42l.52-1.43A5.06 5.06 0 0 1 1.36 10.5L0 9.5v-3l1.36-.5c.11-.4.27-.78.48-1.13L1.32 3.44 3.44 1.32l1.43.52C5.22 1.63 5.6 1.46 6 1.36z M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z"/></svg>
      <span class="nav-label">System</span>
      <span id="sys-badge" class="nav-badge" style="display:none">0</span>
      <span class="kb">4</span>
    </a>
    <a class="nav-link" data-page="charts" onclick="go('charts')">
      <svg viewBox="0 0 16 16" fill="currentColor"><path d="M2 13V5h1.5v8zm3 0V2h1.5v11zm3 0V7h1.5v6zm3 0V4H13v9z"/></svg>
      <span class="nav-label">Charts</span>
      <span class="kb">5</span>
    </a>
    <a class="nav-link" data-page="logs" onclick="go('logs')">
      <svg viewBox="0 0 16 16" fill="currentColor"><path d="M2 2h12v1.5H2zm0 3h12v1.5H2zm0 3h12v1.5H2zm0 3h8v1.5H2z"/></svg>
      <span class="nav-label">Logs</span>
      <span class="kb">6</span>
    </a>
  </div>
  <div class="nav-bottom"><span id="status-dot"></span><span id="status-ts">Connecting&#8230;</span></div>
</nav>

<!-- right side -->
<div class="right">

  <!-- topbar -->
  <div class="topbar">
    <div class="tb-item">
      <div id="tb-status-pill" class="status-pill pill-nodata"><div class="dot"></div><span>No data</span></div>
    </div>
    <div class="tb-item">
      <div>
        <div class="tb-lbl">Equity</div>
        <div class="tb-val neu" id="tb-equity">&#8212;</div>
      </div>
      <canvas id="tb-sparkline" class="tb-sparkline"></canvas>
    </div>
    <div class="tb-item">
      <div>
        <div class="tb-lbl">Daily PnL</div>
        <div class="tb-val neu" id="tb-pnl">&#8212;</div>
      </div>
    </div>
    <div class="tb-item">
      <div class="dd-bar-wrap">
        <div class="tb-lbl">Drawdown</div>
        <div class="dd-bar-track"><div id="tb-dd-fill" class="dd-bar-fill" style="width:0%;background:var(--green)"></div></div>
        <div class="dd-bar-label"><span id="tb-dd-pct">0%</span><span id="tb-dd-limit" style="color:var(--border)">/ 18%</span></div>
      </div>
    </div>
    <div class="tb-item">
      <div>
        <div class="tb-lbl">Positions</div>
        <div class="tb-val sm neu" id="tb-positions">&#8212;</div>
      </div>
    </div>
    <div class="tb-item" style="margin-left:auto;border-right:none;gap:14px">
      <div id="tb-ks" class="tb-ks-ok" onclick="go('system')">&#9679; Systems OK</div>
      <div id="tb-ago" class="fresh">&#8212;</div>
    </div>
  </div>

  <!-- pages -->
  <main>

  <!-- Overview -->
  <div class="page active" id="page-overview">
    <h1>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2 2h5v5H2zm7 0h5v5H9zM2 9h5v5H2zm7 0h5v5H9z"/></svg>
      Overview
    </h1>
    <div class="hero-grid">
      <div class="hero-stat equity">
        <div class="lbl">Account Equity</div>
        <div class="big neu" id="ov-equity">&#8212;</div>
        <div class="sub" id="ov-equity-sub"></div>
      </div>
      <div class="hero-stat" id="ov-pnl-card">
        <div class="lbl">Daily PnL</div>
        <div class="big neu" id="ov-pnl">&#8212;</div>
        <div class="sub" id="ov-pnl-sub"></div>
      </div>
      <div class="hero-stat dd">
        <div class="lbl">Max Drawdown</div>
        <div class="big neu" id="ov-dd">&#8212;</div>
        <div class="sub" id="ov-dd-sub"></div>
      </div>
      <div class="hero-stat pos-count">
        <div class="lbl">Open Positions</div>
        <div class="big neu" id="ov-pos">&#8212;</div>
        <div class="sub" id="ov-pos-sub">of maximum</div>
      </div>
    </div>
    <div class="stat-grid">
      <div class="stat"><div class="lbl">Orders Placed</div><div class="val neu" id="ov-placed">&#8212;</div><div class="sub" id="ov-placed-sub"></div></div>
      <div class="stat"><div class="lbl">Orders Filled</div><div class="val neu" id="ov-filled">&#8212;</div><div class="sub" id="ov-fill-rate"></div></div>
      <div class="stat"><div class="lbl">Orders Cancelled</div><div class="val neu" id="ov-cancelled">&#8212;</div><div class="sub">total</div></div>
      <div class="stat"><div class="lbl">Loop Latency p50</div><div class="val neu" id="ov-lat">&#8212;</div><div class="sub">median</div></div>
      <div class="stat"><div class="lbl">API Errors</div><div class="val neu" id="ov-errs">&#8212;</div><div class="sub">total</div></div>
      <div class="stat"><div class="lbl">WS Reconnects</div><div class="val neu" id="ov-ws">&#8212;</div><div class="sub">since start</div></div>
    </div>
    <div class="chart-row">
      <div class="chart-card"><h2>Equity History</h2><canvas id="equityChart"></canvas></div>
      <div class="chart-card"><h2>Daily PnL History</h2><canvas id="pnlChart"></canvas></div>
    </div>
  </div>

  <!-- Positions -->
  <div class="page" id="page-positions">
    <h1>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 2.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm.5 1.5h-1v3.69l2.53 1.46.75-1.3-2.28-1.32V5z"/></svg>
      Open Positions
    </h1>
    <div class="table-wrap">
      <div class="table-hdr"><h2>Positions</h2><span id="pos-count" class="badge badge-blue">0</span></div>
      <table><thead><tr><th>Coin</th><th>Unrealized PnL</th><th>Regime</th><th>Signal Confidence</th></tr></thead>
      <tbody id="pos-body">
        <tr><td colspan="4"><div class="empty"><svg width="32" height="32" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 2.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9z"/></svg><p>No open positions</p></div></td></tr>
      </tbody></table>
    </div>
  </div>

  <!-- Orders -->
  <div class="page" id="page-orders">
    <h1>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h11A1.5 1.5 0 0 1 15 2.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 1 13.5zm1.5 0v11h11v-11zM4 5h8v1.5H4zm0 3h8v1.5H4zm0 3h5v1.5H4z"/></svg>
      Orders
    </h1>
    <div class="table-wrap">
      <div class="table-hdr"><h2>Order Activity</h2></div>
      <table><thead><tr><th>Type</th><th>Coin</th><th>Side</th><th>Count</th></tr></thead>
      <tbody id="orders-body">
        <tr><td colspan="4"><div class="empty"><svg width="32" height="32" viewBox="0 0 16 16" fill="currentColor"><path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h11A1.5 1.5 0 0 1 15 2.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 1 13.5zm1.5 0v11h11v-11zM4 5h8v1.5H4zm0 3h8v1.5H4zm0 3h5v1.5H4z"/></svg><p>No orders placed yet</p></div></td></tr>
      </tbody></table>
    </div>
    <div class="chart-card" style="margin-bottom:16px"><h2>Fill Slippage Distribution (bps)</h2><canvas id="slippageChart" style="max-height:180px"></canvas></div>
  </div>

  <!-- System -->
  <div class="page" id="page-system">
    <h1>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M6.5 1h3l.5 1.5c.4.1.78.27 1.13.48l1.43-.52 2.12 2.12-.52 1.43c.21.35.37.73.48 1.13L16 7.5v3l-1.5.5c-.11.4-.27.78-.48 1.13l.52 1.43-2.12 2.12-1.43-.52c-.35.21-.73.37-1.13.48L9.5 17h-3l-.5-1.5a5.06 5.06 0 0 1-1.13-.48l-1.43.52L1.32 13.42l.52-1.43A5.06 5.06 0 0 1 1.36 10.5L0 9.5v-3l1.36-.5c.11-.4.27-.78.48-1.13L1.32 3.44 3.44 1.32l1.43.52C5.22 1.63 5.6 1.46 6 1.36z"/></svg>
      System
    </h1>
    <div id="ks-card" class="ks-card ok">
      <div class="ks-card-header">
        <svg id="ks-icon" width="20" height="20" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm3.78 5.03-4.5 4.5a.75.75 0 0 1-1.06 0l-2-2a.75.75 0 1 1 1.06-1.06l1.47 1.47 3.97-3.97a.75.75 0 1 1 1.06 1.06z"/></svg>
        <div>
          <div class="ks-card-title" id="ks-title">Kill switch inactive &#8212; trading active</div>
          <div class="ks-card-detail" id="ks-detail"></div>
        </div>
      </div>
      <div class="ks-actions" id="ks-actions">
        <button class="btn btn-danger" onclick="showKsTriggerModal()" id="btn-ks-trigger">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zM7 4h2v5H7zm0 6h2v2H7z"/></svg>
          Emergency Stop
        </button>
        <button class="btn btn-muted" onclick="showKsResetModal()" id="btn-ks-reset" style="display:none">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 3a5 5 0 1 0 5 5h-1.5A3.5 3.5 0 1 1 8 4.5V3zm1 0h4v4h-1.5V4.56l-.72.72A5 5 0 0 0 8 3z"/></svg>
          Reset Kill Switch
        </button>
        <span id="ks-no-control" style="font-size:11px;color:var(--muted);display:none">Controls unavailable &#8212; dashboard running standalone</span>
      </div>
    </div>
    <div class="stat-grid">
      <div class="stat"><div class="lbl">Loop Latency p50</div><div class="val neu" id="sys-lat50">&#8212;</div><div class="sub">median</div></div>
      <div class="stat"><div class="lbl">Loop Latency p95</div><div class="val neu" id="sys-lat95">&#8212;</div><div class="sub">95th percentile</div></div>
      <div class="stat"><div class="lbl">WS Reconnects</div><div class="val neu" id="sys-ws">&#8212;</div><div class="sub">since start</div></div>
      <div class="stat"><div class="lbl">API Errors</div><div class="val neu" id="sys-errs">&#8212;</div><div class="sub">total</div></div>
    </div>
    <div class="table-wrap">
      <div class="table-hdr"><h2>API Errors by Endpoint</h2></div>
      <table><thead><tr><th>Endpoint</th><th>Error Code</th><th>Count</th></tr></thead>
      <tbody id="errs-body">
        <tr><td colspan="3"><div class="empty" style="padding:20px"><p>No API errors &#8212; all good</p></div></td></tr>
      </tbody></table>
    </div>
  </div>

  <!-- Charts -->
  <div class="page" id="page-charts">
    <div class="chart-page-hdr">
      <div id="coin-tabs" class="coin-tabs"></div>
      <div class="chart-meta-strip">
        <span id="tv-regime" class="badge badge-muted">&#8212;</span>
        <span id="tv-confidence" style="font-size:11px;color:var(--muted)"></span>
        <span id="tv-pnl" style="font-size:11px"></span>
        <span id="tv-interval" style="font-size:11px;color:var(--muted)">15m</span>
      </div>
    </div>
    <div id="tv-container" class="tv-container">
      <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);flex-direction:column;gap:12px">
        <svg width="48" height="48" viewBox="0 0 16 16" fill="currentColor" opacity=".2"><path d="M2 13V5h1.5v8zm3 0V2h1.5v11zm3 0V7h1.5v6zm3 0V4H13v9z"/></svg>
        <p style="font-size:13px">Select a coin above to load the TradingView chart</p>
      </div>
    </div>
  </div>

  <!-- Logs -->
  <div class="page" id="page-logs">
    <h1>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2 2h12v1.5H2zm0 3h12v1.5H2zm0 3h12v1.5H2zm0 3h8v1.5H2z"/></svg>
      Live Logs
    </h1>
    <div class="log-controls">
      <input id="log-filter" placeholder="Filter messages&#8230; (press / to focus)" oninput="renderLogs()">
      <select id="log-level" onchange="renderLogs()">
        <option value="">All levels</option>
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
        <option value="CRITICAL">CRITICAL</option>
      </select>
      <button class="log-toggle" id="autoscroll-btn" onclick="toggleAutoScroll()">Auto-scroll: OFF</button>
      <button class="btn btn-muted" style="font-size:11px;padding:5px 10px" onclick="clearLogFilter()">Clear</button>
    </div>
    <div id="log-stream"><div class="log-line" style="color:var(--muted);padding:20px 14px">Waiting for log entries&#8230;</div></div>
  </div>

  </main>
</div>

<!-- confirm modal -->
<div class="modal-overlay" id="modal-overlay">
  <div class="modal">
    <h3 id="modal-title">Confirm</h3>
    <p id="modal-body">Are you sure?</p>
    <textarea id="modal-reason" placeholder="Reason (optional)" style="display:none"></textarea>
    <div class="modal-actions">
      <button class="btn btn-muted" onclick="closeModal()">Cancel</button>
      <button class="btn btn-danger" id="modal-confirm-btn" onclick="modalConfirm()">Confirm</button>
    </div>
  </div>
</div>

<script>
// ============================================================
// ROUTING
// ============================================================
const PAGES = ['overview','positions','orders','system','charts','logs'];
let currentPage = 'overview';

function go(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  const pageEl = document.getElementById('page-' + page);
  const linkEl = document.querySelector('[data-page="' + page + '"]');
  if (pageEl) pageEl.classList.add('active');
  if (linkEl) linkEl.classList.add('active');
  currentPage = page;
  location.hash = page;
  fetchPage(page);
}

window.addEventListener('hashchange', () => {
  const page = location.hash.slice(1) || 'overview';
  if (PAGES.includes(page)) go(page);
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
  const tag = document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    if (e.key === 'Escape') {
      document.activeElement.blur();
      closeModal();
    }
    return;
  }
  if (e.key === 'Escape') { closeModal(); return; }
  const idx = parseInt(e.key, 10);
  if (idx >= 1 && idx <= 6) { go(PAGES[idx - 1]); return; }
  if (e.key === '/') {
    e.preventDefault();
    go('logs');
    setTimeout(() => { const f = document.getElementById('log-filter'); if (f) f.focus(); }, 50);
  }
});

// Init
const _initPage = location.hash.slice(1) || 'overview';
if (_initPage !== 'overview' && PAGES.includes(_initPage)) go(_initPage);

// ============================================================
// PROGRESS BAR
// ============================================================
let _pbTimer = null;
let _pbStart = 0;
const PB_DURATION = 5000;

function startProgressBar() {
  _pbStart = Date.now();
  const fill = document.getElementById('progress-bar-fill');
  if (_pbTimer) clearInterval(_pbTimer);
  fill.style.transition = 'none';
  fill.style.width = '0%';
  requestAnimationFrame(() => {
    fill.style.transition = 'width ' + PB_DURATION + 'ms linear';
    fill.style.width = '100%';
  });
  _pbTimer = setTimeout(() => { fill.style.width = '0%'; }, PB_DURATION + 100);
}

function resetProgressBar() {
  const fill = document.getElementById('progress-bar-fill');
  if (_pbTimer) clearTimeout(_pbTimer);
  fill.style.transition = 'none';
  fill.style.width = '0%';
  // Restart
  requestAnimationFrame(() => startProgressBar());
}

// ============================================================
// TOAST
// ============================================================
function toast(msg, type, duration) {
  type = type || 'info';
  duration = (duration === undefined) ? 4000 : duration;
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = '<span class="toast-msg">' + msg + '</span><span class="toast-close" onclick="dismissToast(this.parentNode)">&#10005;</span>';
  container.appendChild(el);
  if (duration > 0) {
    setTimeout(() => dismissToast(el), duration);
  }
  return el;
}

function dismissToast(el) {
  if (!el || !el.parentNode) return;
  el.classList.add('removing');
  setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
}

// ============================================================
// ANIMATED NUMBER COUNTERS
// ============================================================
const _animState = {};

function animNum(id, newVal, fmtFn) {
  const el = document.getElementById(id);
  if (!el) return;
  if (newVal == null) { el.textContent = '\u2014'; return; }
  const oldVal = _animState[id] != null ? _animState[id] : newVal;
  _animState[id] = newVal;
  if (oldVal === newVal) return;
  const start = performance.now();
  const dur = 500;
  function step(now) {
    const t = Math.min((now - start) / dur, 1);
    const ease = t < 0.5 ? 2*t*t : -1+(4-2*t)*t;
    const cur = oldVal + (newVal - oldVal) * ease;
    el.textContent = fmtFn(cur);
    if (t < 1) {
      requestAnimationFrame(step);
    } else {
      el.textContent = fmtFn(newVal);
      el.classList.add('pop');
      setTimeout(() => el.classList.remove('pop'), 350);
    }
  }
  requestAnimationFrame(step);
}

// ============================================================
// MINI SPARKLINE (topbar equity)
// ============================================================
const _sparkData = [];
const MAX_SPARK = 40;
let _sparkCtx = null;

function initSparkline() {
  const canvas = document.getElementById('tb-sparkline');
  if (!canvas) return;
  _sparkCtx = canvas.getContext('2d');
}

function drawSparkline() {
  if (!_sparkCtx || _sparkData.length < 2) return;
  const canvas = _sparkCtx.canvas;
  const w = canvas.offsetWidth || 60;
  const h = canvas.offsetHeight || 28;
  canvas.width = w;
  canvas.height = h;
  _sparkCtx.clearRect(0, 0, w, h);
  const min = Math.min(..._sparkData);
  const max = Math.max(..._sparkData);
  const range = max - min || 1;
  const pts = _sparkData.map((v, i) => [
    (i / (_sparkData.length - 1)) * w,
    h - ((v - min) / range) * (h - 4) - 2
  ]);
  _sparkCtx.beginPath();
  _sparkCtx.moveTo(pts[0][0], pts[0][1]);
  pts.slice(1).forEach(([x, y]) => _sparkCtx.lineTo(x, y));
  _sparkCtx.strokeStyle = '#58a6ff';
  _sparkCtx.lineWidth = 1.5;
  _sparkCtx.stroke();
}

// ============================================================
// AGO TICKER
// ============================================================
let _lastFetchMs = 0;
let _prevKsState = null;

function tickAgo() {
  const el = document.getElementById('tb-ago');
  if (!el) return;
  if (!_lastFetchMs) { el.textContent = '\u2014'; return; }
  const secs = Math.round((Date.now() - _lastFetchMs) / 1000);
  el.textContent = secs + 's ago';
  el.className = secs < 8 ? 'fresh' : secs > 20 ? 'stale' : '';
}

setInterval(tickAgo, 1000);

// ============================================================
// KILL SWITCH STATE CHANGE
// ============================================================
function checkKsChange(triggered) {
  if (_prevKsState === null) { _prevKsState = triggered; return; }
  if (triggered && !_prevKsState) {
    toast('\u26A0\uFE0F KILL SWITCH TRIGGERED \u2014 all trading halted', 'error', 0);
    document.title = '\u26A0 HALTED \u2014 AutoTrader';
  } else if (!triggered && _prevKsState) {
    toast('Kill switch reset \u2014 trading resumed', 'success', 5000);
    document.title = 'AutoTrader';
  }
  _prevKsState = triggered;
}

// ============================================================
// FORMATTERS
// ============================================================
const fmt$ = v => v == null ? '\u2014' : '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
const fmtPnL = v => v == null ? '\u2014' : (v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
const fmtMs = v => v == null ? '\u2014' : v.toFixed(0) + 'ms';
const fmtPct = v => v == null ? '\u2014' : (v * 100).toFixed(2) + '%';
const fmtInt = v => v == null ? '\u2014' : Math.round(v).toLocaleString();
function colorClass(v) { return v == null ? 'neu' : v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu'; }
function setVal(id, txt, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = txt;
  if (cls) { const base = el.className.replace(/\b(pos|neg|neu|warn)\b/g, '').trim(); el.className = base + ' ' + cls; }
}
function regimeBadge(r) {
  const map = {trend_up:'badge-green', trend_down:'badge-red', range:'badge-blue', high_vol:'badge-yellow', low_vol:'badge-muted', unknown:'badge-muted'};
  return '<span class="badge ' + (map[r] || 'badge-muted') + '">' + (r || '\u2014') + '</span>';
}
function highlight(text, filter) {
  if (!filter || !text) return text || '';
  const esc = filter.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return text.replace(new RegExp('(' + esc + ')', 'gi'), '<mark class="hl">$1</mark>');
}

// ============================================================
// CHART.JS SETUP
// ============================================================
const MAX_H = 120;
const eqHist = [], pnlHist = [], tsLabels = [];
const _chartDefaults = {
  responsive: true, maintainAspectRatio: true, animation: false,
  plugins: {legend: {display: false}},
  elements: {point: {radius: 0}},
  scales: {
    x: {display: false},
    y: {grid: {color: '#21262d'}, ticks: {color: '#7d8590', font: {size: 11}}}
  }
};

const equityChart = new Chart(document.getElementById('equityChart'), {
  type: 'line',
  data: {labels: tsLabels, datasets: [{data: eqHist, borderColor: '#58a6ff', borderWidth: 1.5, fill: true, backgroundColor: 'rgba(88,166,255,.07)', tension: .3}]},
  options: _chartDefaults,
});
const pnlChart = new Chart(document.getElementById('pnlChart'), {
  type: 'bar',
  data: {labels: tsLabels, datasets: [{data: pnlHist, backgroundColor: []}]},
  options: _chartDefaults,
});
let slippageChart = null;

// ============================================================
// TOPBAR UPDATE
// ============================================================
let _lastSuccessTs = 0;
let _ksTriggered = false;
const DD_LIMIT = 0.18;

function updateTopbar(d) {
  _lastFetchMs = Date.now();
  _lastSuccessTs = _lastFetchMs;
  const eq = d.account_equity_usd, pnl = d.daily_pnl_usd, dd = d.max_drawdown_pct;
  const ksTriggered = d.kill_switch_triggered || false;
  _ksTriggered = ksTriggered;
  checkKsChange(ksTriggered);

  // Animated equity
  if (eq != null) {
    animNum('tb-equity', eq, v => '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}));
    _sparkData.push(eq);
    if (_sparkData.length > MAX_SPARK) _sparkData.shift();
    drawSparkline();
  } else {
    document.getElementById('tb-equity').textContent = '\u2014';
  }

  // Animated PnL
  if (pnl != null) {
    animNum('tb-pnl', pnl, v => (v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}));
    document.getElementById('tb-pnl').className = 'tb-val ' + colorClass(pnl);
  } else {
    document.getElementById('tb-pnl').textContent = '\u2014';
  }

  // Drawdown bar
  if (dd != null) {
    const pct = Math.min(Math.abs(dd) / DD_LIMIT, 1.0);
    const fill = document.getElementById('tb-dd-fill');
    fill.style.width = (pct * 100) + '%';
    const ddAbs = Math.abs(dd);
    fill.style.background = ddAbs > DD_LIMIT * 0.8 ? 'var(--red)' : ddAbs > DD_LIMIT * 0.5 ? 'var(--yellow)' : 'var(--green)';
    document.getElementById('tb-dd-pct').textContent = fmtPct(dd);
    document.getElementById('tb-dd-limit').textContent = '/ ' + (DD_LIMIT * 100).toFixed(0) + '%';
  }

  // Positions
  const pos = d.active_positions;
  document.getElementById('tb-positions').textContent = pos != null ? pos : '\u2014';

  // Status pill
  const pill = document.getElementById('tb-status-pill');
  if (ksTriggered) {
    pill.className = 'status-pill pill-halted';
    pill.innerHTML = '<div class="dot"></div><span>HALTED</span>';
  } else {
    pill.className = 'status-pill pill-running';
    pill.innerHTML = '<div class="dot"></div><span>Running</span>';
  }

  // KS indicator
  const tbKs = document.getElementById('tb-ks');
  if (ksTriggered) {
    tbKs.className = 'tb-ks-alert';
    tbKs.textContent = '\u26A0 KILL SWITCH ACTIVE';
  } else if ((d.api_errors_total || 0) > 0) {
    tbKs.className = 'tb-ks-ok';
    tbKs.style.color = 'var(--yellow)';
    tbKs.textContent = '\u26A0 ' + d.api_errors_total + ' errors';
  } else {
    tbKs.className = 'tb-ks-ok';
    tbKs.style.color = 'var(--green)';
    tbKs.textContent = '\u25CF Systems OK';
  }

  // Nav badge
  updateSysBadge(d.api_errors_total || 0, ksTriggered);

  // Status bar
  document.getElementById('status-dot').className = '';
  document.getElementById('status-ts').textContent = new Date().toLocaleTimeString();
}

function updateSysBadge(errCount, ksTriggered) {
  const badge = document.getElementById('sys-badge');
  if (ksTriggered) {
    badge.style.display = 'inline-block';
    badge.className = 'nav-badge';
    badge.textContent = 'KS';
  } else if (errCount > 0) {
    badge.style.display = 'inline-block';
    badge.className = 'nav-badge warn';
    badge.textContent = errCount;
  } else {
    badge.style.display = 'none';
  }
}

// ============================================================
// DATA FETCHERS
// ============================================================
let _rawLogs = [];

async function fetchTopbar() {
  resetProgressBar();
  try {
    const r = await fetch('/api/overview');
    if (!r.ok) return;
    const d = await r.json();
    updateTopbar(d);
  } catch(e) {
    document.getElementById('status-dot').className = 'dead';
  }
}

async function fetchPage(page) {
  try {
    const r = await fetch('/api/' + page);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    if (page === 'overview') renderOverview(d);
    else if (page === 'positions') renderPositions(d);
    else if (page === 'orders') renderOrders(d);
    else if (page === 'system') renderSystem(d);
    else if (page === 'charts') renderCharts(d);
    else if (page === 'logs') { _rawLogs = d.logs || []; renderLogs(); }
  } catch(e) {
    document.getElementById('status-dot').className = 'dead';
    if (Date.now() - _lastSuccessTs > 30000) {
      const pill = document.getElementById('tb-status-pill');
      if (!_ksTriggered) {
        pill.className = 'status-pill pill-nodata';
        pill.innerHTML = '<div class="dot"></div><span>No data</span>';
      }
    }
  }
}

// ============================================================
// RENDER: OVERVIEW
// ============================================================
function renderOverview(d) {
  updateTopbar(d);
  const eq = d.account_equity_usd, pnl = d.daily_pnl_usd, dd = d.max_drawdown_pct;

  // Hero: equity
  animNum('ov-equity', eq, v => '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}));
  document.getElementById('ov-equity').className = 'big neu';
  document.getElementById('ov-equity-sub').textContent = eq != null ? 'total account value' : 'no data yet';

  // Hero: PnL
  animNum('ov-pnl', pnl, v => (v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}));
  document.getElementById('ov-pnl').className = 'big ' + colorClass(pnl);
  const pnlCard = document.getElementById('ov-pnl-card');
  pnlCard.className = 'hero-stat ' + (pnl == null ? '' : pnl >= 0 ? 'pnl-pos' : 'pnl-neg');

  // Hero: DD
  const ddEl = document.getElementById('ov-dd');
  ddEl.textContent = fmtPct(dd);
  const ddAbs = Math.abs(dd || 0);
  ddEl.className = 'big ' + (ddAbs > DD_LIMIT * 0.8 ? 'neg' : ddAbs > DD_LIMIT * 0.5 ? 'warn' : 'pos');
  document.getElementById('ov-dd-sub').textContent = 'limit: ' + (DD_LIMIT * 100).toFixed(0) + '%';

  // Hero: positions
  const pos = d.active_positions;
  animNum('ov-pos', pos, v => Math.round(v).toString());
  document.getElementById('ov-pos').className = 'big neu';

  // Stat row
  const placed = d.orders_placed_total, filled = d.orders_filled_total, cancelled = d.orders_cancelled_total;
  animNum('ov-placed', placed, fmtInt);
  document.getElementById('ov-placed-sub').textContent = 'total since start';
  animNum('ov-filled', filled, fmtInt);
  const fillRate = document.getElementById('ov-fill-rate');
  if (fillRate && placed > 0) fillRate.textContent = 'fill rate: ' + ((filled / placed) * 100).toFixed(0) + '%';
  animNum('ov-cancelled', cancelled, fmtInt);
  animNum('ov-lat', d.loop_latency_p50_ms, v => v.toFixed(0) + 'ms');
  animNum('ov-errs', d.api_errors_total, fmtInt);
  document.getElementById('ov-errs').className = 'val ' + ((d.api_errors_total || 0) > 0 ? 'neg' : 'neu');
  animNum('ov-ws', d.ws_reconnects_total, fmtInt);

  // History charts
  const ts = new Date().toLocaleTimeString();
  tsLabels.push(ts); eqHist.push(eq); pnlHist.push(pnl);
  if (tsLabels.length > MAX_H) { tsLabels.shift(); eqHist.shift(); pnlHist.shift(); }
  pnlChart.data.datasets[0].backgroundColor = pnlHist.map(v => (v || 0) >= 0 ? 'rgba(63,185,80,.7)' : 'rgba(248,81,73,.7)');
  equityChart.update('none');
  pnlChart.update('none');
}

// ============================================================
// RENDER: POSITIONS
// ============================================================
function renderPositions(d) {
  const rows = d.positions || [];
  const tbody = document.getElementById('pos-body');
  const countEl = document.getElementById('pos-count');
  if (countEl) countEl.textContent = rows.length;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4"><div class="empty"><svg width="32" height="32" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 2.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9z"/></svg><p>No open positions \u2014 bot is scanning for signals</p></div></td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const rc = (r.regime || '').replace(/[^a-z_]/g, '');
    return '<tr class="regime-' + rc + '">' +
      '<td><b>' + (r.coin || '\u2014') + '</b></td>' +
      '<td class="' + colorClass(r.pnl_usd) + '">' + fmtPnL(r.pnl_usd) + '</td>' +
      '<td>' + regimeBadge(r.regime) + '</td>' +
      '<td>' + (r.confidence != null ? (r.confidence * 100).toFixed(0) + '%' : '\u2014') + '</td>' +
      '</tr>';
  }).join('');
}

// ============================================================
// RENDER: ORDERS
// ============================================================
function renderOrders(d) {
  const rows = d.by_label || [];
  const tbody = document.getElementById('orders-body');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4"><div class="empty"><svg width="32" height="32" viewBox="0 0 16 16" fill="currentColor"><path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h11A1.5 1.5 0 0 1 15 2.5v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 1 13.5zm1.5 0v11h11v-11zM4 5h8v1.5H4zm0 3h8v1.5H4zm0 3h5v1.5H4z"/></svg><p>No orders placed yet \u2014 waiting for signals</p></div></td></tr>';
  } else {
    const typeColors = {placed: 'badge-blue', filled: 'badge-green', cancelled: 'badge-yellow'};
    tbody.innerHTML = rows.map(r => {
      const type = r.metric.replace('orders_', '').replace('_total', '');
      return '<tr>' +
        '<td><span class="badge ' + (typeColors[type] || 'badge-muted') + '">' + type + '</span></td>' +
        '<td><b>' + (r.labels.coin || '\u2014') + '</b></td>' +
        '<td>' + (r.labels.side ? '<span class="badge ' + (r.labels.side === 'buy' ? 'badge-green' : 'badge-red') + '">' + r.labels.side + '</span>' : '\u2014') + '</td>' +
        '<td>' + r.value + '</td>' +
        '</tr>';
    }).join('');
  }

  // Slippage chart
  const buckets = d.slippage_buckets || [];
  const labels = ['0.5', '1', '2', '5', '10', '20', '50', '\u221e'];
  const datasets = buckets.map((b, i) => {
    const counts = b.buckets.map((item, j) => j === 0 ? item[1] : (item[1] - (b.buckets[j-1] ? b.buckets[j-1][1] : 0)));
    const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149'];
    return {label: b.coin, data: counts, backgroundColor: colors[i % colors.length] + '88', borderColor: colors[i % colors.length], borderWidth: 1};
  });
  if (slippageChart) slippageChart.destroy();
  slippageChart = new Chart(document.getElementById('slippageChart'), {
    type: 'bar',
    data: {labels, datasets: datasets.length ? datasets : [{label: 'No fills yet', data: new Array(8).fill(0), backgroundColor: '#21262d'}]},
    options: Object.assign({}, _chartDefaults, {
      scales: Object.assign({}, _chartDefaults.scales, {x: {ticks: {color: '#7d8590', font: {size: 11}}, grid: {color: '#21262d'}}}),
      plugins: {legend: {display: datasets.length > 1, labels: {color: '#7d8590', font: {size: 11}}}}
    })
  });
}

// ============================================================
// RENDER: SYSTEM
// ============================================================
function renderSystem(d) {
  const ks = d.kill_switch || {};
  const card = document.getElementById('ks-card');
  const title = document.getElementById('ks-title');
  const detail = document.getElementById('ks-detail');
  const btnTrigger = document.getElementById('btn-ks-trigger');
  const btnReset = document.getElementById('btn-ks-reset');
  const noControl = document.getElementById('ks-no-control');

  if (ks.triggered) {
    card.className = 'ks-card triggered';
    title.textContent = '\u26A0 Kill switch ACTIVE \u2014 ' + (ks.trigger || 'unknown');
    detail.textContent = (ks.details || '') + (ks.timestamp ? '  \u00B7  ' + new Date(ks.timestamp).toLocaleString() : '');
    if (btnTrigger) btnTrigger.style.display = 'none';
    if (btnReset) btnReset.style.display = '';
  } else {
    card.className = 'ks-card ok';
    title.textContent = 'Kill switch inactive \u2014 trading active';
    detail.textContent = '';
    if (btnTrigger) btnTrigger.style.display = '';
    if (btnReset) btnReset.style.display = 'none';
  }

  if (d.kill_switch_controllable === false) {
    if (btnTrigger) btnTrigger.style.display = 'none';
    if (btnReset) btnReset.style.display = 'none';
    if (noControl) noControl.style.display = '';
  } else {
    if (noControl) noControl.style.display = 'none';
  }

  animNum('sys-lat50', d.loop_latency_p50_ms, v => v.toFixed(0) + 'ms');
  animNum('sys-lat95', d.loop_latency_p95_ms, v => v.toFixed(0) + 'ms');
  document.getElementById('sys-lat95').className = 'val ' + ((d.loop_latency_p95_ms || 0) > 1000 ? 'warn' : 'neu');
  const ws = d.ws_reconnects || 0;
  animNum('sys-ws', ws, fmtInt);
  document.getElementById('sys-ws').className = 'val ' + (ws > 5 ? 'warn' : 'neu');
  const errs = d.api_errors || [];
  const total = errs.reduce((s, e) => s + e.value, 0);
  animNum('sys-errs', total, fmtInt);
  document.getElementById('sys-errs').className = 'val ' + (total > 0 ? 'neg' : 'neu');
  const tbody = document.getElementById('errs-body');
  if (!errs.length) {
    tbody.innerHTML = '<tr><td colspan="3"><div class="empty" style="padding:20px"><p>No API errors \u2014 all good</p></div></td></tr>';
  } else {
    tbody.innerHTML = errs.map(e =>
      '<tr>' +
      '<td><code style="font-size:11px">' + (e.labels.endpoint || '\u2014') + '</code></td>' +
      '<td><span class="badge badge-red">' + (e.labels.code || '\u2014') + '</span></td>' +
      '<td class="neg">' + e.value + '</td>' +
      '</tr>'
    ).join('');
  }
}

// ============================================================
// RENDER: LOGS
// ============================================================
let _autoScroll = false;

function toggleAutoScroll() {
  _autoScroll = !_autoScroll;
  const btn = document.getElementById('autoscroll-btn');
  btn.textContent = 'Auto-scroll: ' + (_autoScroll ? 'ON' : 'OFF');
  btn.className = 'log-toggle' + (_autoScroll ? ' on' : '');
}

function clearLogFilter() {
  document.getElementById('log-filter').value = '';
  document.getElementById('log-level').value = '';
  renderLogs();
}

function renderLogs() {
  const filter = document.getElementById('log-filter').value.toLowerCase();
  const level = document.getElementById('log-level').value;
  const container = document.getElementById('log-stream');
  const lines = _rawLogs
    .filter(r => (!level || (r.level || r.log_level || 'INFO').toUpperCase() === level))
    .filter(r => !filter || JSON.stringify(r).toLowerCase().includes(filter));
  if (!lines.length) {
    container.innerHTML = '<div class="log-line" style="color:var(--muted);padding:20px 14px">' +
      (_rawLogs.length ? 'No entries match your filter' : 'Waiting for log entries\u2026') + '</div>';
    return;
  }
  container.innerHTML = lines.map(r => {
    const ts = r.timestamp || r.ts || '';
    const lvl = (r.level || r.log_level || 'INFO').toUpperCase();
    const event = r.event || r.message || r.msg || '';
    const eventHl = filter ? highlight(event, filter) : event;
    const ctx = Object.entries(r)
      .filter(([k]) => !['timestamp', 'ts', 'level', 'log_level', 'event', 'message', 'msg'].includes(k))
      .map(([k, v]) => k + '=<span style="color:var(--blue)">' + JSON.stringify(v) + '</span>')
      .join(' ');
    return '<div class="log-line">' +
      '<span class="log-ts">' + (ts.slice(11, 19) || '\u2014') + '</span>' +
      '<span class="log-lvl ' + lvl + '">' + lvl + '</span>' +
      '<span class="log-event">' + eventHl + '</span>' +
      (ctx ? '<span class="log-ctx"> ' + ctx + '</span>' : '') +
      '</div>';
  }).join('');
  if (_autoScroll) container.scrollTop = container.scrollHeight;
}

// ============================================================
// RENDER: CHARTS (TradingView)
// ============================================================
const TV_SYMBOLS = {
  BTC:'BYBIT:BTCUSDT.P', ETH:'BYBIT:ETHUSDT.P', SOL:'BYBIT:SOLUSDT.P',
  ARB:'BYBIT:ARBUSDT.P', AVAX:'BYBIT:AVAXUSDT.P', BNB:'BYBIT:BNBUSDT.P',
  DOGE:'BYBIT:DOGEUSDT.P', MATIC:'BYBIT:MATICUSDT.P', OP:'BYBIT:OPUSDT.P',
  LINK:'BYBIT:LINKUSDT.P', ATOM:'BYBIT:ATOMUSDT.P', APT:'BYBIT:APTUSDT.P',
  SUI:'BYBIT:SUIUSDT.P', TIA:'BYBIT:TIAUSDT.P', INJ:'BYBIT:INJUSDT.P',
  WIF:'BYBIT:WIFUSDT.P', PEPE:'BYBIT:PEPEUSDT.P', SEI:'BYBIT:SEIUSDT.P',
};
const TV_INTERVALS = {'1m':'1','3m':'3','5m':'5','15m':'15','30m':'30','1h':'60','2h':'120','4h':'240','1d':'D','1w':'W'};
function tvSymbol(coin) { return TV_SYMBOLS[coin] || ('BYBIT:' + coin + 'USDT.P'); }

let _tvSelectedCoin = null, _tvChartData = {};

function renderCharts(d) {
  _tvChartData = d;
  const coins = Object.keys(d).sort();
  const tabsEl = document.getElementById('coin-tabs');
  const existing = [...tabsEl.querySelectorAll('.coin-tab')].map(t => t.dataset.coin);
  if (JSON.stringify(existing) !== JSON.stringify(coins)) {
    tabsEl.innerHTML = coins.map(c =>
      '<button class="coin-tab' + (c === _tvSelectedCoin ? ' active' : '') + '" data-coin="' + c + '" onclick="selectCoin(\'' + c + '\')">' + c + '</button>'
    ).join('') || '<span style="font-size:12px;color:var(--muted)">No coins available yet</span>';
  }
  if (!_tvSelectedCoin && coins.length) selectCoin(coins[0]);
  else if (_tvSelectedCoin) _updateMeta(_tvSelectedCoin);
}

function selectCoin(coin) {
  _tvSelectedCoin = coin;
  document.querySelectorAll('.coin-tab').forEach(t => t.classList.toggle('active', t.dataset.coin === coin));
  _loadTVWidget(coin);
  _updateMeta(coin);
}

function _updateMeta(coin) {
  const cd = _tvChartData[coin];
  if (!cd) return;
  const regime = cd.regime || 'unknown';
  const map = {trend_up:'badge-green', trend_down:'badge-red', range:'badge-blue', high_vol:'badge-yellow', low_vol:'badge-muted', unknown:'badge-muted'};
  const regEl = document.getElementById('tv-regime');
  regEl.className = 'badge ' + (map[regime] || 'badge-muted');
  regEl.textContent = regime.replace('_', ' ');
  document.getElementById('tv-confidence').textContent = cd.confidence != null ? (cd.confidence * 100).toFixed(0) + '% conf' : '';
  document.getElementById('tv-interval').textContent = cd.interval || '15m';
  const pnl = cd.pnl_usd;
  const pnlEl = document.getElementById('tv-pnl');
  if (pnl != null) {
    pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    pnlEl.textContent = fmtPnL(pnl) + ' PnL';
  } else { pnlEl.textContent = ''; }
}

function _loadTVWidget(coin) {
  const container = document.getElementById('tv-container');
  container.innerHTML = '';
  const symbol = tvSymbol(coin);
  const cd = _tvChartData[coin];
  const interval = TV_INTERVALS[cd ? cd.interval || '15m' : '15m'] || '15';
  const script = document.createElement('script');
  script.src = 'https://s3.tradingview.com/tv.js';
  script.onload = function() {
    const widgetDiv = document.createElement('div');
    widgetDiv.id = 'tv-widget-inner';
    container.appendChild(widgetDiv);
    new TradingView.widget({
      container_id: 'tv-widget-inner',
      width: '100%', height: '100%',
      symbol: symbol, interval: interval,
      timezone: 'Utc', theme: 'dark', style: '1', locale: 'en',
      toolbar_bg: '#161b22',
      enable_publishing: false, hide_top_toolbar: false,
      hide_legend: false, hide_side_toolbar: false,
      allow_symbol_change: true, save_image: false,
      studies: ['RSI@tv-basicstudies', 'MACD@tv-basicstudies'],
      overrides: {
        'paneProperties.background': '#0d0f14',
        'paneProperties.backgroundType': 'solid',
        'scalesProperties.textColor': '#7d8590',
        'scalesProperties.lineColor': '#21262d',
        'paneProperties.separatorColor': '#21262d',
      },
    });
  };
  container.appendChild(script);
}

// ============================================================
// KILL SWITCH MODALS
// ============================================================
let _modalAction = null;

function showModal(title, body, confirmLabel, confirmClass, action, showReason) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').textContent = body;
  const btn = document.getElementById('modal-confirm-btn');
  btn.textContent = confirmLabel;
  btn.className = 'btn ' + confirmClass;
  const reasonEl = document.getElementById('modal-reason');
  reasonEl.style.display = showReason ? '' : 'none';
  reasonEl.value = '';
  _modalAction = action;
  document.getElementById('modal-overlay').classList.add('open');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  _modalAction = null;
}

async function modalConfirm() {
  if (_modalAction) await _modalAction();
  closeModal();
}

document.getElementById('modal-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

function showKsTriggerModal() {
  showModal(
    '\u26A0 Emergency Stop',
    'This will immediately halt all trading and cancel all open orders. The kill switch can be reset from this page.',
    'Trigger Emergency Stop',
    'btn-danger',
    async function() {
      const reason = document.getElementById('modal-reason').value || 'Manual trigger from dashboard';
      try {
        const r = await fetch('/api/kill-switch/trigger', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({reason: reason})});
        const d = await r.json();
        if (d.ok) { fetchPage('system'); toast('Kill switch triggered', 'error', 5000); }
        else toast('Failed: ' + (d.error || 'unknown error'), 'error', 6000);
      } catch(e) { toast('Request failed: ' + e.message, 'error', 6000); }
    },
    true
  );
}

function showKsResetModal() {
  showModal(
    'Reset Kill Switch',
    'This will re-enable trading. Make sure the underlying issue has been resolved before resetting.',
    'Reset & Resume Trading',
    'btn-warn',
    async function() {
      const reason = document.getElementById('modal-reason').value || 'Reset from dashboard';
      try {
        const r = await fetch('/api/kill-switch/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({reason: reason})});
        const d = await r.json();
        if (d.ok) { fetchPage('system'); toast('Kill switch reset \u2014 trading resumed', 'success', 5000); }
        else toast('Failed: ' + (d.error || 'unknown error'), 'error', 6000);
      } catch(e) { toast('Request failed: ' + e.message, 'error', 6000); }
    },
    true
  );
}

// ============================================================
// INIT
// ============================================================
initSparkline();
startProgressBar();

// Topbar poll (always runs)
setInterval(fetchTopbar, 5000);
// Active page poll
setInterval(function() { fetchPage(currentPage); }, 5000);

// Initial load
fetchTopbar();
fetchPage(currentPage);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/overview", _api_overview)
    app.router.add_get("/api/positions", _api_positions)
    app.router.add_get("/api/orders", _api_orders)
    app.router.add_get("/api/system", _api_system)
    app.router.add_get("/api/charts", _api_charts)
    app.router.add_get("/api/logs", _api_logs)
    app.router.add_post("/api/kill-switch/trigger", _api_ks_trigger)
    app.router.add_post("/api/kill-switch/reset", _api_ks_reset)
    return app


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_dashboard(port: int = 8080) -> None:
    """Start the dashboard in a background daemon thread (non-blocking).

    Uses AppRunner + TCPSite instead of web.run_app so that signal handlers
    (which only work on the main thread) are never installed.
    """

    async def _serve() -> None:
        app = _make_app()
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        # Run indefinitely until the thread is killed (daemon thread)
        while True:
            await asyncio.sleep(3600)

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())

    threading.Thread(target=_run, daemon=True, name="dashboard").start()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoTrader dashboard")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    print(f"Dashboard → http://localhost:{args.port}")
    web.run_app(_make_app(), port=args.port)
