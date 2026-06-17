# Aegis — Changelog

All notable changes to this project are documented in this file.

Format: `[YYYY-MM-DD] — summary` with bullet details per release/session.

---

## [Unreleased] — Options Execution v1.0 (Complete Stack)

### Added
- `app/trading_controls.py` — portal-editable runtime toggles persisted to `data/trading_controls.json`
- `GET/PATCH /api/settings/trading`, `POST /api/settings/trading/reset` — no restart required
- Settings page **Trading Controls** tile — options algo, futures engine, sheet gate toggles
- `frontend/src/components/OptionsAlgoPanel.tsx` — dashboard panel for automated iron condor: enabled state, open structures, MTM estimate, regime gates, manual close
- `frontend/src/api/types.ts` — `OptionsAlgoStatus`, `OptionsAlgoStructure`, `OptionsAlgoLeg` types (expected `GET /api/options/algo/status` shape)
- `frontend/src/api/client.ts` — `getOptionsAlgoStatus()`, `closeOptionsAlgoStructure()` (`POST /api/options/algo/close`)
- `frontend/src/index.css` — Bloomberg-style styles for options algo panel

### Changed
- `frontend/src/pages/Dashboard.tsx` — `OptionsAlgoPanel` below Live Options Desk (dashboard / API integration)
- `get_options_algo_status_payload()` in `app/options_strategy_runner.py` — enabled flags, open structures, `structures_today`, `last_cycle_result`, regime gate summary
- `record_options_cycle_result()` — every `run_options_cycle()` outcome persisted for dashboard
- Dashboard status/SSE: `options_algo` field alongside existing `options_legs` on `/api/status`, `/api/status/quick`, `/api/status/stream`
- `GET /api/options/algo/status` — dedicated automated structures status
- `POST /api/options/algo/close/{structure_id}` — paper-safe manual flatten via `options_execution_engine.close_structure`

### Added
- `app/options_iv.py` — live ATM/near-ATM CE+PE IV from `kite.quote` + BS implied-vol solver; blended IV with VIX fallback; leg premium enrichment from live quotes
- `app/options_chain.py` — `resolve_strikes_from_chain()` snaps iron condor strikes to listed chain values
- `app/options_eod_flatten.py` — EOD close for open options structures (`is_eod_flatten_window` or options `session_end`); state in `data/options_eod_flatten_state.json`; env `OPTIONS_EOD_FLATTEN`
- Trade ledger events `options.structure.open` / `options.structure.close` (structure_id, underlying, credit, legs) wired in `options_execution_engine`
- `CHANGELOG.md` — project-wide change log (update on every session)
- `app/kite_margins_basket.py` — Kite `basket_order_margins` pre-trade gate with paper fallback
- `app/options_execution_engine.py` — 4-leg atomic coordinator; all legs via RiskGatekeeper
- `app/options_positions.py` — persistent open-structure state (`data/options_structures.json`)
- `app/options_strategy_runner.py` — Iron Condor regime gates, entry/exit cycle for main loop
- `config/strategy_config.yaml` — `options:` section (trading gates, regime, iron condor params)
- `config_loader.get_options_config()` — YAML loader for options execution settings
- `scripts/algo_lab_ops.py chain-archive` — daily NIFTY/BANKNIFTY/SENSEX chain archive command
- `tests/test_kite_margins_basket.py` — margin basket unit tests
- `tests/test_options_execution.py` — execution engine, regime gates, rollback tests
- `.env.example` — `FUTURES_TRADING_ENABLED`, `OPTIONS_TRADING_ENABLED`, `OPTIONS_STRATEGY_INTERVAL`

### Changed
- `app/options_strategy_runner.py` — `build_iron_condor_proposal` uses live chain IV + quote premiums when Kite is available; falls back to India VIX proxy
- `app/strategies/iron_condor.py` — `research_only=False` allowed for execution path
- `app/main.py` — futures pause flag; automated options cycle in main loop; `maybe_run_options_eod_flatten` in main loop when options trading enabled
- `app/config_loader.py` — optional `options.session_end` for EOD flatten fallback
- `app/ops_hub.py` — compliance checks for options execution stack
- `tests/test_iron_condor.py` — execution mode test (replaces research_only block test)

### How to enable options paper trading
1. Set `options.trading_enabled: true` in `config/strategy_config.yaml`
2. Set `OPTIONS_TRADING_ENABLED=true` in `.env`
3. Keep `FORCE_DRY_RUN=true` for paper
4. Run `python run.py` — watch `[OPTIONS] OPENED` in terminal

### Changed
- `options_strategy_runner` / `main.py` / `run.py` — respect portal trading controls before env/YAML
- `get_external_signals_config()` — merges portal overrides for sheet enabled/mode

### Integration fixes (subagent merge)
- `frontend/src/api/client.ts` — close URL fixed to `POST /api/options/algo/close/{structure_id}`
- `frontend/src/api/types.ts` + `OptionsAlgoPanel.tsx` — aligned with backend `enabled` object, `regime_gates.allowed/passed`, `mtm`/`last_cycle_result`
- `get_options_algo_status_payload()` — flat aliases + `mtm_estimate` aggregate + leg `role` labels
- `app/main.py` — terminal summary for open automated structures
- `config/strategy_config.yaml` — `options.session_end` for EOD flatten

### Verified (prior session)
- `pytest tests/test_kite_margins_basket.py tests/test_options_execution.py tests/test_iron_condor.py` — 21 passed
- `pytest tests/test_ops_hub.py tests/test_ops_api.py` — 16 passed