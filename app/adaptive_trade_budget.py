"""
Regime-aware adaptive trade budget — disciplined extension, not greed.

Base cap per index (default 3). Up to +2 bonus trades when regime quality is strong:
  +1 from score >= 0.65 (no green session required)
  +2nd from score >= 0.82 (green session P&L required)
Hard ceiling per index and portfolio-wide cap prevent runaway overtrading.
Chop/ranging/fake-breakout windows stay at base cap only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AdaptiveBudgetConfig:
    enabled: bool = True
    base_trades_per_symbol: int = 3
    max_bonus_trades: int = 2
    hard_ceiling_per_symbol: int = 5
    max_portfolio_trades: int = 12
    min_score_for_bonus: float = 0.65
    min_score_for_second_bonus: float = 0.82
    require_positive_session_pnl_for_bonus: bool = True
    require_positive_symbol_pnl_for_bonus: bool = False


@dataclass
class TradeBudget:
    symbol: str
    base_cap: int
    bonus_available: int
    effective_cap: int
    hard_ceiling: int
    portfolio_cap: int
    regime_score: float
    trades_used: int
    portfolio_trades: int
    bonus_granted: bool
    reasons: List[str] = field(default_factory=list)
    status: str = "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "base_cap": self.base_cap,
            "bonus_available": self.bonus_available,
            "effective_cap": self.effective_cap,
            "hard_ceiling": self.hard_ceiling,
            "portfolio_cap": self.portfolio_cap,
            "regime_score": round(self.regime_score, 3),
            "trades_used": self.trades_used,
            "portfolio_trades": self.portfolio_trades,
            "bonus_granted": self.bonus_granted,
            "reasons": self.reasons,
            "status": self.status,
        }


DEFAULT_CONFIG = AdaptiveBudgetConfig()


def compute_regime_score(
    regime: Optional[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
) -> tuple[float, List[str]]:
    """0–1 quality score for allowing a small trade-count extension."""
    regime = regime or {}
    context = context or {}
    reasons: List[str] = []
    score = 0.50

    trend = regime.get("trend", "ranging")
    vol = regime.get("volatility", "normal")
    htf = regime.get("htf_bias", "neutral")

    if trend in ("uptrend", "downtrend"):
        score += 0.22
        reasons.append("trending market")
    elif trend == "ranging":
        score -= 0.28
        reasons.append("chop — no extension")

    if vol == "normal":
        score += 0.18
        reasons.append("normal volatility")
    elif vol == "low":
        score += 0.06
    elif vol == "high":
        score -= 0.18
        reasons.append("high vol — cautious")

    if htf in ("bullish", "bearish"):
        score += 0.12
        reasons.append("HTF bias aligned")

    if context.get("vol_ok"):
        score += 0.12
        reasons.append("volume confirmed")

    entry_conf = context.get("entry_confidence")
    if entry_conf is not None and float(entry_conf) >= 0.65:
        score += 0.14
        reasons.append("strong breakout")

    learning_mult = float(context.get("learning_mult", 1.0) or 1.0)
    if learning_mult < 0.85:
        score -= 0.22
        reasons.append("memory de-risk active")
    elif learning_mult >= 0.98:
        score += 0.08

    if context.get("is_expiry_day"):
        score -= 0.18
        reasons.append("expiry day")
    if not context.get("safe_trading_window", True):
        score -= 0.12
        reasons.append("outside safe window")

    if int(context.get("consecutive_losses", 0) or 0) >= 2:
        score -= 0.30
        reasons.append("loss streak — no extension")

    return max(0.0, min(1.0, score)), reasons


def compute_trade_budget(
    symbol: str,
    regime: Optional[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
    config: Optional[AdaptiveBudgetConfig] = None,
) -> TradeBudget:
    """Effective per-symbol entry cap for today."""
    cfg = config or DEFAULT_CONFIG
    context = context or {}
    score, reasons = compute_regime_score(regime, context)

    trades_used = int(context.get("trades_used", 0) or 0)
    portfolio_trades = int(context.get("portfolio_trades", 0) or 0)
    daily_pnl = float(context.get("daily_pnl", 0) or 0)
    symbol_pnl = float(context.get("symbol_daily_pnl", 0) or 0)
    capital = float(context.get("capital", 1_000_000) or 1_000_000)

    base = cfg.base_trades_per_symbol
    posture_max = int(context.get("recommended_max_trades", base) or base)
    base = min(base, posture_max)

    if daily_pnl < -0.01 * capital:
        base = max(1, base - 1)
        reasons.append("session loss >1% — cap reduced")
    elif daily_pnl < -0.005 * capital:
        base = max(2, base - 1)
        reasons.append("session loss >0.5% — cap reduced")

    bonus = 0
    bonus_granted = False
    trend = (regime or {}).get("trend", "ranging")

    if cfg.enabled and trend != "ranging":
        if score >= cfg.min_score_for_second_bonus:
            bonus = min(2, cfg.max_bonus_trades)
            if (
                bonus >= 2
                and cfg.require_positive_session_pnl_for_bonus
                and daily_pnl <= 0
            ):
                bonus = min(1, cfg.max_bonus_trades)
                reasons.append("2nd bonus needs green session P&L — +1 only")
        elif score >= cfg.min_score_for_bonus:
            bonus = min(1, cfg.max_bonus_trades)

        if cfg.require_positive_symbol_pnl_for_bonus and symbol_pnl <= 0:
            bonus = 0
            if score >= cfg.min_score_for_bonus:
                reasons.append("bonus needs green symbol P&L")

        if bonus > 0:
            bonus_granted = True
            reasons.append(f"quality window +{bonus}")

    effective = min(base + bonus, cfg.hard_ceiling_per_symbol)

    if trades_used >= effective:
        status = "at_ceiling"
    elif trades_used >= base and bonus_granted:
        status = "quality_extension"
    elif trades_used >= base:
        status = "at_base"
    else:
        status = "ok"

    return TradeBudget(
        symbol=symbol,
        base_cap=base,
        bonus_available=bonus,
        effective_cap=effective,
        hard_ceiling=cfg.hard_ceiling_per_symbol,
        portfolio_cap=cfg.max_portfolio_trades,
        regime_score=score,
        trades_used=trades_used,
        portfolio_trades=portfolio_trades,
        bonus_granted=bonus_granted,
        reasons=reasons,
        status=status,
    )


def portfolio_budget_summary(budgets: Dict[str, TradeBudget]) -> Dict[str, Any]:
    """Aggregate view for dashboard."""
    if not budgets:
        return {
            "portfolio_trades": 0,
            "portfolio_cap": DEFAULT_CONFIG.max_portfolio_trades,
            "per_symbol": {},
        }
    first = next(iter(budgets.values()))
    return {
        "portfolio_trades": first.portfolio_trades,
        "portfolio_cap": first.portfolio_cap,
        "per_symbol": {sym: b.to_dict() for sym, b in budgets.items()},
    }