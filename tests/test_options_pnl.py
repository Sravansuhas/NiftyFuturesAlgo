from app.options_pnl import compute_lot_pnl, options_round_turn_cost, summarize_sheet_pnl


def test_compute_lot_pnl_gain_and_loss_net():
    pnl = compute_lot_pnl(
        entry=180,
        target=230,
        stop_loss_pts=7,
        ltp=195,
        lot_size=65,
        journal_status="watching",
    )
    assert pnl.gain_gross == (230 - 180) * 65
    assert pnl.loss_gross == 7 * 65
    assert pnl.gain_net is not None
    assert pnl.loss_net is not None
    assert pnl.gain_net < pnl.gain_gross
    assert pnl.loss_net < 0
    assert pnl.lot_price_inr == 195 * 65


def test_compute_lot_pnl_mtm_when_entered():
    pnl = compute_lot_pnl(
        entry=180,
        target=230,
        stop_loss_pts=7,
        ltp=200,
        lot_size=30,
        journal_status="entered",
        entry_fill=182,
    )
    assert pnl.mtm_gross == (200 - 182) * 30
    assert pnl.mtm_net is not None
    assert pnl.mtm_net < pnl.mtm_gross


def test_options_round_turn_cost_includes_stt():
    cost = options_round_turn_cost(180, 230, 65)
    assert cost >= 40 + 22  # brokerage + buffer


def test_summarize_sheet_pnl():
    sheet = {
        "indices": {
            "NIFTY": {
                "call": {
                    "strike": 23100,
                    "entry": 180,
                    "journal_status": "entered",
                    "mtm_net_1lot": 500,
                    "mtm_gross_1lot": 550,
                    "gain_net_1lot": 3000,
                    "loss_net_1lot": -500,
                },
                "put": {},
            },
        },
    }
    summary = summarize_sheet_pnl(sheet)
    assert summary["legs"] == 1
    assert summary["in_trade"] == 1
    assert summary["mtm_net"] == 500
    assert summary["max_gain_net_if_all_hit"] == 3000