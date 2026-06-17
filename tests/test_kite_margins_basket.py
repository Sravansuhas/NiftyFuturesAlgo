from app.kite_margins_basket import (
    build_margin_order_params,
    simulate_basket_margin,
)


def _sample_legs():
    return [
        {
            "tradingsymbol": "NIFTY26JUN24000PE",
            "transaction_type": "BUY",
            "quantity": 65,
            "exchange": "NFO",
            "premium": 120.0,
            "strike": 24000,
        },
        {
            "tradingsymbol": "NIFTY26JUN24200PE",
            "transaction_type": "SELL",
            "quantity": 65,
            "exchange": "NFO",
            "premium": 200.0,
            "strike": 24200,
        },
    ]


def test_build_margin_order_params():
    params = build_margin_order_params(_sample_legs()[0], product="NRML")
    assert params["exchange"] == "NFO"
    assert params["tradingsymbol"] == "NIFTY26JUN24000PE"
    assert params["transaction_type"] == "BUY"
    assert params["product"] == "NRML"
    assert params["quantity"] == 65


def test_simulate_basket_margin_kite_success():
    class FakeKite:
        def basket_order_margins(self, orders, consider_positions=True):
            return {
                "initial": {"total": 100_000},
                "final": {"total": 40_000},
            }

    result = simulate_basket_margin(
        FakeKite(),
        _sample_legs(),
        capital=1_000_000,
        max_margin_pct=0.15,
    )
    assert result.approved is True
    assert result.final_margin == 40_000


def test_simulate_basket_margin_exceeds_cap():
    class FakeKite:
        def basket_order_margins(self, orders, consider_positions=True):
            return {"initial": {"total": 200_000}, "final": {"total": 200_000}}

    result = simulate_basket_margin(
        FakeKite(),
        _sample_legs(),
        capital=1_000_000,
        max_margin_pct=0.15,
    )
    assert result.approved is False
    assert any("exceeds cap" in r for r in result.reasons)


def test_simulate_basket_margin_paper_fallback():
    result = simulate_basket_margin(
        None,
        _sample_legs(),
        capital=1_000_000,
        max_margin_pct=0.15,
        dry_run_fallback=True,
    )
    assert result.approved is True
    assert result.final_margin > 0