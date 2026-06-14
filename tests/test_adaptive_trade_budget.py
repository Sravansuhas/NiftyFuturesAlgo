from app.adaptive_trade_budget import (
    AdaptiveBudgetConfig,
    compute_regime_score,
    compute_trade_budget,
)


def test_ranging_blocks_bonus():
    budget = compute_trade_budget(
        "NIFTY",
        {"trend": "ranging", "volatility": "normal", "htf_bias": "bullish"},
        {"daily_pnl": 5000, "capital": 1_000_000, "trades_used": 2},
    )
    assert budget.effective_cap == 3
    assert budget.bonus_available == 0
    assert "chop" in " ".join(budget.reasons).lower()


def test_trending_strong_grants_double_bonus_with_green_session():
    cfg = AdaptiveBudgetConfig(enabled=True, max_bonus_trades=2)
    budget = compute_trade_budget(
        "NIFTY",
        {"trend": "uptrend", "volatility": "normal", "htf_bias": "bullish"},
        {
            "daily_pnl": 2000,
            "capital": 1_000_000,
            "trades_used": 3,
            "vol_ok": True,
            "entry_confidence": 0.72,
            "learning_mult": 1.0,
            "safe_trading_window": True,
            "is_expiry_day": False,
            "consecutive_losses": 0,
        },
        config=cfg,
    )
    assert budget.regime_score >= 0.82
    assert budget.effective_cap == 5
    assert budget.bonus_available == 2
    assert budget.bonus_granted is True


def test_moderate_trend_grants_first_bonus_without_green_session():
    budget = compute_trade_budget(
        "BANKNIFTY",
        {"trend": "uptrend", "volatility": "normal", "htf_bias": "bullish"},
        {
            "daily_pnl": 0,
            "capital": 1_000_000,
            "trades_used": 3,
            "vol_ok": True,
            "entry_confidence": 0.66,
            "learning_mult": 1.0,
            "safe_trading_window": True,
            "is_expiry_day": False,
            "consecutive_losses": 0,
        },
    )
    assert budget.regime_score >= 0.65
    assert budget.effective_cap == 4
    assert budget.bonus_available == 1


def test_second_bonus_needs_green_session():
    budget = compute_trade_budget(
        "BANKNIFTY",
        {"trend": "uptrend", "volatility": "normal", "htf_bias": "bullish"},
        {
            "daily_pnl": 0,
            "capital": 1_000_000,
            "trades_used": 3,
            "vol_ok": True,
            "entry_confidence": 0.80,
            "learning_mult": 1.0,
            "safe_trading_window": True,
            "is_expiry_day": False,
            "consecutive_losses": 0,
        },
    )
    assert budget.regime_score >= 0.82
    assert budget.effective_cap == 4
    assert budget.bonus_available == 1
    assert any("green session" in r.lower() for r in budget.reasons)


def test_loss_streak_reduces_score():
    base, _ = compute_regime_score({"trend": "uptrend", "volatility": "normal"}, {})
    score, reasons = compute_regime_score(
        {"trend": "uptrend", "volatility": "normal"},
        {"consecutive_losses": 3},
    )
    assert score < base
    assert any("loss streak" in r for r in reasons)


def test_daily_loss_reduces_base_cap():
    budget = compute_trade_budget(
        "BANKNIFTY",
        {"trend": "uptrend", "volatility": "normal"},
        {"daily_pnl": -12_000, "capital": 1_000_000, "trades_used": 1},
    )
    assert budget.base_cap <= 2