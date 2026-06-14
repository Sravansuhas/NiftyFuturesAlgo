"""
Fill quality learning — compares Kite fills against cost model assumptions.

Phase 4: snapshots persisted for dashboard + weekly earn reports.
Read-only on trading logic.
"""

from __future__ import annotations

import json
import logging
import statistics as stats
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILL_LEARNING_DIR = PROJECT_ROOT / "data" / "fill_learning"

_FALLBACK_LOTS = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}


def _detect_underlying(symbol: str) -> Optional[str]:
    sym = (symbol or "").upper()
    if "BANKNIFTY" in sym:
        return "BANKNIFTY"
    if "SENSEX" in sym:
        return "SENSEX"
    if "NIFTY" in sym and "BANK" not in sym:
        return "NIFTY"
    return None


def _get_lot_size(underlying: str) -> int:
    key = (underlying or "NIFTY").upper()
    try:
        from app.instruments_manager import instruments_manager

        lot = instruments_manager.get_lot_size(key)
        if lot and lot > 0:
            return int(lot)
    except Exception:
        pass
    return _FALLBACK_LOTS.get(key, 65)


def analyze_fills(
    raw_trades: List[Dict],
    *,
    underlying: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Analyze index futures fills via cost model (dashboard + improvement APIs)."""
    return analyze_fills_for_learning(raw_trades, limit=limit, underlying=underlying)


def analyze_fills_for_learning(
    raw_trades: List[Dict],
    limit: int = 50,
    underlying: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze index futures fills via cost model (shared with dashboard)."""
    from backtesting.costs import CostConfig, TransactionCostModel

    cost_model = TransactionCostModel(CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=55.0,
        default_slippage_points=4.0,
    ))

    filter_key = (underlying or "").upper()
    index_trades = []
    for trade in raw_trades:
        sym = str(trade.get("tradingsymbol", "")).upper()
        if "FUT" not in sym:
            continue
        detected = _detect_underlying(sym)
        if not detected:
            continue
        if filter_key:
            if filter_key == "NIFTY" and detected == "NIFTY":
                index_trades.append(trade)
            elif filter_key != "NIFTY" and detected == filter_key:
                index_trades.append(trade)
        else:
            index_trades.append(trade)

    index_trades = index_trades[:limit]
    analyzed: List[Dict[str, Any]] = []
    total_est_cost = 0.0
    hours: List[float] = []
    fills_by_underlying: Dict[str, int] = {}

    for trade in index_trades:
        try:
            sym = str(trade.get("tradingsymbol", "")).upper()
            detected = _detect_underlying(sym) or "NIFTY"
            lot_size = _get_lot_size(detected)
            qty = int(trade.get("quantity", 0) or 0)
            price = float(trade.get("average_price") or trade.get("price") or 0)
            ts = (
                trade.get("order_timestamp")
                or trade.get("fill_timestamp")
                or trade.get("exchange_timestamp")
            )
            fill_hour = None
            if isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    fill_hour = dt.hour + dt.minute / 60.0
                    hours.append(fill_hour)
                except Exception:
                    pass

            lots = max(1, qty // lot_size) if lot_size > 0 else 1
            est_round_cost = cost_model.round_turn_cost_per_lot(
                slippage_points=None,
                entry_price=price or None,
            ) * lots
            total_est_cost += est_round_cost
            fills_by_underlying[detected] = fills_by_underlying.get(detected, 0) + 1

            analyzed.append({
                "symbol": trade.get("tradingsymbol"),
                "underlying": detected,
                "qty": qty,
                "lots": lots,
                "lot_size": lot_size,
                "price": round(price, 2),
                "ts": str(ts),
                "fill_hour": fill_hour,
                "type": trade.get("transaction_type"),
                "est_cost_round_turn_rs": round(est_round_cost, 2),
            })
        except Exception:
            continue

    notes = build_calibration_notes({
        "summary": {
            "fills_analyzed": len(analyzed),
            "est_total_cost_rs": round(total_est_cost, 2),
            "fills_by_underlying": fills_by_underlying,
        },
        "fills": analyzed,
    })

    n = len(analyzed)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "underlying": filter_key or "ALL",
        "fills": analyzed,
        "summary": {
            "fills_analyzed": n,
            "nifty_fills_analyzed": fills_by_underlying.get("NIFTY", 0),
            "est_total_cost_rs": round(total_est_cost, 2),
            "avg_est_cost_per_fill": round(total_est_cost / max(1, n), 2),
            "fills_by_underlying": fills_by_underlying,
        },
        "documentation_notes": notes,
        "source": "kite.trades() + backtest cost model v1",
    }


def build_calibration_notes(analysis: Dict[str, Any]) -> List[str]:
    """Actionable notes for backtest cost model tuning (sample-size aware)."""
    summary = analysis.get("summary") or {}
    fills = analysis.get("fills") or []
    n = int(summary.get("fills_analyzed", 0) or 0)
    total_cost = float(summary.get("est_total_cost_rs", 0) or 0)
    notes: List[str] = []

    if n == 0:
        notes.append("No index futures fills found. Paper trading or no recent executions.")
        return notes

    notes.append(f"Analyzed {n} index futures fills via cost model.")
    notes.append(f"Model-estimated total round-turn costs: ~₹{total_cost:,.0f}.")

    hours = [f.get("fill_hour") for f in fills if f.get("fill_hour") is not None]
    if hours:
        avg_h = stats.mean(hours)
        if 9.25 <= avg_h <= 10.0 or avg_h >= 15.0:
            notes.append("Many fills occurred near session edges (toxic window per strategy rules).")
        else:
            notes.append(f"Avg fill hour ~{avg_h:.1f} IST — inside preferred window.")

    if n >= 8:
        notes.append(
            "[LEARNING] Sufficient real fills for cost calibration. "
            "Compare model vs actual STT+GST line items. "
            "If real costs 15%+ higher in high-vol, increase default_slippage in CostConfig."
        )
    else:
        notes.append(
            "[LEARNING] Small sample — run more paper/live trades then re-analyze before trusting calibration."
        )

    return notes


def record_fill_learning_snapshot(analysis: Dict[str, Any]) -> Path:
    """Persist fill learning analysis to data/fill_learning/."""
    payload = {**analysis, "saved_at": datetime.utcnow().isoformat()}
    return fill_learning_store.save_snapshot(payload)


def load_latest_fill_learning() -> Optional[Dict[str, Any]]:
    return fill_learning_store.load_latest()


class FillLearningStore:
    def __init__(self, store_dir: Path = FILL_LEARNING_DIR):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def build_snapshot_from_kite(self, kite) -> Dict[str, Any]:
        try:
            raw = kite.trades() or []
        except Exception as exc:
            return {
                "error": str(exc),
                "error_code": "KITE_TRADES_FAILED",
                "fills": [],
                "summary": {"fills_analyzed": 0},
                "documentation_notes": [f"Kite trades pull failed: {exc}"],
            }
        snapshot = analyze_fills_for_learning(raw)
        snapshot["snapshot_type"] = "kite_trades"
        return snapshot

    def save_snapshot(self, snapshot: Dict[str, Any]) -> Path:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.store_dir / f"fill_learning_{ts}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, default=str)
        latest_path = self.store_dir / "latest.json"
        with latest_path.open("w", encoding="utf-8") as handle:
            json.dump({**snapshot, "saved_path": str(path)}, handle, indent=2, default=str)
        return path

    def load_latest(self) -> Optional[Dict[str, Any]]:
        latest_path = self.store_dir / "latest.json"
        if latest_path.exists():
            try:
                with latest_path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                pass
        files = sorted(self.store_dir.glob("fill_learning_*.json"), reverse=True)
        for path in files:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                continue
        return None


fill_learning_store = FillLearningStore()