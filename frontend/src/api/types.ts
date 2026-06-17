/** F&O mood zone labels from the market mood engine */
export type FoMoodZone =
  | 'chop_trap'
  | 'trend_ok'
  | 'range_bound'
  | 'neutral'
  | 'risk_off'
  | 'elevated_chop'
  | 'favorable'
  | string;

export interface FoMoodComponent {
  id: string;
  label: string;
  score: number;
  weight?: number;
  contribution?: number;
  detail?: string;
  zone?: FoMoodZone;
}

export interface FoMoodIndexRow {
  symbol: string;
  trend?: string;
  algo_trend?: string;
  brother_bias?: string;
  chop_score?: number;
  proposed?: string;
  tape_mood?: number;
  tradeability?: number;
  tape_zone?: FoMoodZone;
  tradeability_zone?: FoMoodZone;
  guard_allowed?: boolean;
}

export interface FoMoodMacroBlock {
  vix?: {
    available?: boolean;
    level?: number;
    previous_close?: number;
    change_pct?: number;
    zone?: string;
    fetched_at?: string;
  };
  fii_dii?: {
    available?: boolean;
    trade_date?: string;
    fii_net_crores?: number;
    dii_net_crores?: number;
    flow_bias?: string;
    fetched_at?: string;
  };
}

export interface FoMarketMoodSnapshot {
  timestamp?: string;
  tape_mood: number;
  tradeability: number;
  tape_zone?: FoMoodZone;
  tradeability_zone?: FoMoodZone;
  divergence?: number;
  human_summary?: string;
  algo_summary?: string;
  mismatch?: boolean;
  mismatch_detail?: string;
  components?: FoMoodComponent[];
  indices?: Record<string, FoMoodIndexRow>;
  macro?: FoMoodMacroBlock;
  available?: boolean;
  cached?: boolean;
  error?: string;
}

export interface MarketStatus {
  session_status?: string;
  is_market_open?: boolean;
  is_safe_trading_window?: boolean;
  within_pre_event_block_window?: boolean;
  hours_to_high_impact_event?: number;
  next_high_impact_event?: { id?: string; name?: string; category?: string; hours_away?: number };
  is_expiry_day?: boolean;
  timestamp?: string;
  error?: string;
}

export interface EquityPoint {
  ts: number;
  equity: number;
}

export interface PerSymbolStatus {
  position: number;
  avg_price: number;
  daily_pnl: number;
  daily_trades: number;
  daily_loss: number;
  live_unrealized_pnl?: number;
}

export interface LiveSnapshot {
  symbol?: string;
  contract?: string;
  index_key?: string;
  exchange?: string;
  spot_ltp?: number;
  spot_basis?: number;
  ltp?: number;
  atr?: number;
  fast_atr?: number;
  proposed?: string;
  target?: number;
  stop_loss?: number;
  confidence?: number;
  data_source?: string;
  data_age_seconds?: number;
  unrealized_pnl?: number;
  gate_summary?: string;
  last_update?: string;
  regime?: { volatility?: string; trend?: string };
}

export type ExternalJournalStatus =
  | 'watching'
  | 'entered'
  | 'target_met'
  | 'stop_hit'
  | 'incomplete'
  | 'skipped'
  | 'expired';

export interface ExternalOptionSide {
  entry?: number | null;
  target?: number | null;
  stop_loss?: number | null;
  strike?: number | null;
  status?: string;
  remarks?: string;
  journal_status?: ExternalJournalStatus;
  entry_fill?: number | null;
  last_ltp?: number | null;
  session_high?: number | null;
  session_low?: number | null;
  checked_at?: string | null;
  target_met_at?: string | null;
  stop_hit_at?: string | null;
  entered_at?: string | null;
  outcome_note?: string;
  lot_size?: number | null;
  premium?: number | null;
  lot_price_inr?: number | null;
  gain_gross_1lot?: number | null;
  loss_gross_1lot?: number | null;
  gain_net_1lot?: number | null;
  loss_net_1lot?: number | null;
  costs_round_turn?: number | null;
  mtm_gross_1lot?: number | null;
  mtm_net_1lot?: number | null;
}

export interface OptionsPnlSummary {
  legs?: number;
  in_trade?: number;
  mtm_gross?: number;
  mtm_net?: number;
  max_gain_net_if_all_hit?: number;
  max_loss_net_if_all_stop?: number;
}

export interface OptionsMtmSnapshot {
  date?: string;
  available?: boolean;
  mtm_net?: number;
  mtm_gross?: number;
  legs?: number;
  in_trade?: number;
  max_gain_net_if_all_hit?: number;
  max_loss_net_if_all_stop?: number;
}

export interface ExternalJournalRow {
  date: string;
  index: string;
  display_name: string;
  leg: 'call' | 'put';
  option_type: 'CE' | 'PE';
  strike?: number | null;
  entry?: number | null;
  target?: number | null;
  stop_loss?: number | null;
  journal_status?: ExternalJournalStatus;
  last_ltp?: number | null;
  session_high?: number | null;
  session_low?: number | null;
  entry_fill?: number | null;
  outcome_note?: string;
  target_met_at?: string | null;
  stop_hit_at?: string | null;
  entered_at?: string | null;
  checked_at?: string | null;
}

export interface ExternalSignalsSheet {
  date: string;
  updated_at?: string;
  notes?: string;
  indices: Record<string, { call: ExternalOptionSide; put: ExternalOptionSide }>;
  pnl_summary?: OptionsPnlSummary;
}

export interface SheetVsAlgoIndexRow {
  symbol: string;
  sheet_pnl: number;
  algo_pnl: number;
  winner: 'sheet' | 'algo' | 'tie';
  sheet_bias?: string;
  sheet_legs?: number;
  sheet_targets?: number;
  sheet_stops?: number;
  algo_trades?: number;
  algo_position?: number;
}

export interface SheetVsAlgoComparison {
  date: string;
  available: boolean;
  integration_mode?: string;
  integration_enabled?: boolean;
  manual_total_pnl: number;
  algo_total_pnl: number;
  overall_winner: 'sheet' | 'algo' | 'tie';
  per_index: SheetVsAlgoIndexRow[];
  notes?: string;
  config?: Record<string, unknown>;
}

export interface OptionsLegSnapshot {
  leg_id: string;
  index: string;
  leg: 'call' | 'put';
  option_type: 'CE' | 'PE';
  display_name: string;
  strike?: number | null;
  entry?: number | null;
  target?: number | null;
  stop_loss?: number | null;
  last_ltp?: number | null;
  session_high?: number | null;
  session_low?: number | null;
  journal_status?: ExternalJournalStatus;
  tradingsymbol?: string | null;
  data_source?: string;
  data_age_seconds?: number;
  sparkline?: number[];
  outcome_note?: string;
  mtm_net_1lot?: number | null;
}

export interface OptionsLegsPayload {
  available: boolean;
  date?: string;
  legs: Record<string, OptionsLegSnapshot>;
  summary?: OptionsPnlSummary;
  subscribed_tokens?: number;
}

/** Single leg on an automated options structure (iron condor, etc.) */
export interface OptionsAlgoLeg {
  role?: 'put_long' | 'put_short' | 'call_short' | 'call_long' | string;
  underlying?: string;
  option_type?: 'CE' | 'PE' | string;
  strike?: number | null;
  transaction_type?: 'BUY' | 'SELL' | string;
  quantity?: number | null;
  premium?: number | null;
  expiry?: string | null;
  tradingsymbol?: string | null;
  last_ltp?: number | null;
  exchange?: string | null;
}

/** Open iron condor / multi-leg structure tracked by options_position_store */
export interface OptionsAlgoStructure {
  structure_id: string;
  structure_type: string;
  underlying: string;
  status: 'OPEN' | 'CLOSED' | 'FAILED' | string;
  entry_credit?: number | null;
  max_loss?: number | null;
  expiry?: string | null;
  opened_at?: string | null;
  closed_at?: string | null;
  close_reason?: string | null;
  mtm?: number | null;
  mtm_estimate?: number | null;
  legs?: OptionsAlgoLeg[];
  economics?: Record<string, unknown>;
}

export interface OptionsAlgoEnabledFlags {
  options_trading?: boolean;
  config_trading_enabled?: boolean;
  env_trading_enabled?: boolean;
  futures_trading?: boolean;
}

export interface OptionsAlgoConfigBlock {
  underlying?: string;
  product?: string;
  allowed_structures?: string[];
  max_structures_per_day?: number;
  evaluation_interval_sec?: number;
}

/** 0 = clear, 1 = caution (entries restricted), 2 = blocked */
export type GammaCautionLevel = 0 | 1 | 2;

/** Regime gate snapshot from options_strategy_runner.check_regime_gates */
export interface OptionsAlgoRegimeGates {
  allowed?: boolean;
  passed?: boolean;
  reasons?: string[];
  vix_level?: number | null;
  underlying?: string;
  is_expiry_day?: boolean;
  expiry_caution?: boolean;
  expiry_entry_cutoff_hour?: number;
  /** Primary gate severity — prefer over legacy passed/expiry_caution when present */
  gamma_caution_level?: GammaCautionLevel;
  /** Machine trigger id, e.g. expiry_morning_window | expiry_cutoff | gamma_proxy */
  trigger_type?: string;
  /** Human-oriented expiry trigger tags from the runner */
  expiry_triggers?: string[];
  /** Alias / companion list for expiry-specific reasons */
  expiry_reasons?: string[];
  gates?: Record<string, unknown>;
}

export interface OptionsAlgoMtmEstimate {
  total?: number | null;
  structures?: number;
  available?: boolean;
}

export interface OptionsAlgoLastCycle {
  action?: string | null;
  skipped?: boolean;
  success?: boolean;
  message?: string;
  reason?: string;
  details?: string[];
  exit_reason?: string;
}

/** GET /api/options/algo/status — automated iron condor runner snapshot */
export interface OptionsAlgoStatus {
  available?: boolean;
  timestamp?: string;
  enabled?: boolean | OptionsAlgoEnabledFlags;
  config?: OptionsAlgoConfigBlock;
  config_enabled?: boolean;
  env_enabled?: boolean;
  futures_trading_enabled?: boolean;
  underlying?: string;
  allowed_structures?: string[];
  product?: string;
  max_structures_per_day?: number;
  open_count?: number;
  structures_today?: number;
  session_date?: string;
  regime_gates?: OptionsAlgoRegimeGates;
  mtm_estimate?: OptionsAlgoMtmEstimate;
  open_structures?: OptionsAlgoStructure[];
  last_cycle?: OptionsAlgoLastCycle | null;
  last_cycle_result?: OptionsAlgoLastCycle | null;
  last_cycle_at?: string | null;
  error?: string;
}

export interface OptionsAlgoCloseResult {
  success?: boolean;
  ok?: boolean;
  structure_id?: string;
  message?: string;
  error?: string;
  closed?: Record<string, unknown>;
  leg_results?: Record<string, unknown>[];
}

/** Single CE or PE ATM ticker leg on the options desk */
export interface OptionsDeskTickerLeg {
  option_type?: 'CE' | 'PE' | string;
  strike?: number | null;
  ltp?: number | null;
  prev_close?: number | null;
  change?: number | null;
  change_pct?: number | null;
  expiry?: string | null;
  tradingsymbol?: string | null;
  exchange?: string | null;
  live?: boolean;
  data_source?: string;
  data_age_seconds?: number | null;
}

/** One index row: spot, ATM strike, CE + PE tickers */
export interface OptionsDeskTickerRow {
  underlying: string;
  label?: string;
  spot?: number | null;
  spot_change?: number | null;
  spot_change_pct?: number | null;
  atm_strike?: number | null;
  expiry?: string | null;
  live?: boolean;
  ce?: OptionsDeskTickerLeg | null;
  pe?: OptionsDeskTickerLeg | null;
}

/** GET /api/options/desk/tickers — live ATM option tickers for NIFTY / BANKNIFTY / SENSEX */
export interface OptionsDeskTickers {
  available?: boolean;
  timestamp?: string;
  session_date?: string;
  indices?: OptionsDeskTickerRow[] | Record<string, OptionsDeskTickerRow>;
  subscribed_tokens?: number;
  error?: string;
}

/** GET /api/settings/trading — portal runtime controls */
export interface TradingControlsStatus {
  available?: boolean;
  timestamp?: string;
  persisted?: {
    options_trading_enabled?: boolean | null;
    futures_trading_enabled?: boolean | null;
    options_eod_flatten_enabled?: boolean | null;
    external_signals_enabled?: boolean | null;
    external_signals_mode?: string | null;
    updated_at?: string | null;
    updated_by?: string | null;
    file?: string;
  };
  effective?: {
    options_trading_enabled?: boolean;
    futures_trading_enabled?: boolean;
    options_eod_flatten_enabled?: boolean;
    external_signals_enabled?: boolean;
    external_signals_mode?: string;
    force_dry_run?: boolean;
    paper_mode?: boolean;
    live_trading_confirmed?: boolean;
  };
  yaml_defaults?: Record<string, boolean>;
  env?: Record<string, string>;
  notes?: string[];
}

export interface TradingControlsPatchResult {
  success: boolean;
  message?: string;
  changed?: Record<string, boolean | string>;
  status?: TradingControlsStatus;
  allowed?: string[];
}

export interface RecentExecution {
  ts?: string | number;
  type: string;
  side?: string;
  symbol?: string;
  price?: number;
  reason?: string;
  regime?: string;
  qty?: number;
  quantity?: number;
  structure_id?: string;
}

export interface TradeBudgetInfo {
  symbol: string;
  base_cap: number;
  bonus_available: number;
  effective_cap: number;
  hard_ceiling: number;
  portfolio_cap: number;
  regime_score: number;
  trades_used: number;
  portfolio_trades: number;
  bonus_granted: boolean;
  reasons: string[];
  status: string;
}

export interface TradeBudgetSummary {
  portfolio_trades?: number;
  portfolio_cap?: number;
  adaptive_enabled?: boolean;
  per_symbol?: Record<string, TradeBudgetInfo>;
}

export interface FoGuardSymbolSnapshot {
  symbol: string;
  allowed: boolean;
  block_reason?: string;
  blocked_rule?: string;
  risk_multiplier?: number;
  active_guards?: Array<{ id: string; label: string }>;
  highlights?: Record<string, unknown>;
}

export interface FoGuardSnapshot {
  symbols?: Record<string, FoGuardSymbolSnapshot>;
  any_blocked?: boolean;
  portfolio_block_reason?: string;
}

export interface SymbolPosture {
  posture?: string;
  market_color?: string;
  recommended_max_trades_per_day?: number;
  risk_multiplier_hint?: number;
  exit_mode?: string;
  breakout_buffer_bias?: string;
  reasons?: string[];
  contingencies?: string[];
  regime?: { trend?: string; volatility?: string; htf_bias?: string; chop_score?: number };
}

export interface PostureSnapshot {
  portfolio?: {
    posture?: string;
    market_color?: string;
    risk_multiplier_hint?: number;
    reasons?: string[];
    contingencies?: string[];
  };
  per_symbol?: Record<string, SymbolPosture>;
}

export interface TradingJournalSummary {
  date_ist?: string;
  quality_score?: number;
  daily_pnl?: number;
  trade_count?: number;
  feedback_summary?: string;
  note_count?: number;
}

export interface TradingJournalEntry {
  date_ist?: string;
  generated_at?: string;
  session_summary?: {
    quality_score?: number;
    quality_grade?: string;
    quality_components?: Record<string, number>;
    event_metrics?: Record<string, unknown>;
    risk_snapshot?: {
      daily_pnl?: number;
      trades_today?: number;
      per_symbol?: Record<string, unknown>;
    };
  };
  trades?: Array<{
    symbol?: string;
    side?: string;
    quantity?: number;
    realized_pnl?: number;
    exit_reason?: string;
    ts?: number;
  }>;
  trade_count?: number;
  system_feedback?: {
    headline?: string;
    notes?: string[];
    actions?: string[];
    score_context?: string;
  };
  feedback_summary?: string;
  improvement_actions?: string[];
  trader_notes?: Array<{ text: string; added_at?: string }>;
  overnight_context?: Record<string, unknown>;
  macro_context?: Record<string, unknown>;
}

export interface PromotionIndexInsight {
  passed?: boolean;
  status?: string;
  fold_pass_count?: number;
  overlay_eligible?: boolean;
  overlay_reason?: string;
}

export interface MultiIndexWfoInsight {
  has_report?: boolean;
  run_id?: string;
  finished_at?: string;
  report_path?: string;
  summary?: { passed_count?: number; index_count?: number };
  per_index?: Record<
    string,
    {
      has_record?: boolean;
      passed?: boolean;
      status?: string;
      avg_pf?: number;
      avg_return?: number;
    }
  >;
}

export interface PendingProposalInsight {
  id?: string;
  proposal_id?: string;
  description?: string;
  severity?: string;
  proposal_type?: string;
  underlying?: string;
  status?: string;
}

export interface AgentInsights {
  generated_at?: string;
  date_ist?: string;
  promotion_status?: Record<string, PromotionIndexInsight>;
  promotion_summary?: { any_passed?: boolean; all_passed?: boolean };
  multi_index_wfo?: MultiIndexWfoInsight;
  pending_proposals?: {
    count?: number;
    proposals?: PendingProposalInsight[];
    directory?: string;
  };
  lunar_context?: {
    available?: boolean;
    phase_name?: string;
    tithi_name?: string;
    paksha?: string;
    illumination_pct?: number;
    folklore_tag?: string;
  };
  market_context?: {
    available?: boolean;
    path?: string;
    source?: string;
    payload?: Record<string, unknown>;
  };
  documentation_notes?: string[];
  founder_actions?: string[];
  human_gate_required?: boolean;
  posture?: Record<string, unknown>;
  session?: Record<string, unknown>;
  learning_multipliers?: Record<string, { multiplier?: number; reasons?: string[] }>;
  error?: string;
}

export interface SystemStatus {
  timestamp: string;
  engine_ready?: boolean;
  mode: 'PAPER' | 'LIVE';
  state: string;
  capital: number;
  daily_pnl: number;
  combined_daily_pnl?: number;
  options_mtm?: OptionsMtmSnapshot;
  trade_budget?: TradeBudgetSummary;
  fo_guards?: FoGuardSnapshot;
  daily_loss: number;
  current_equity: number;
  trades_today: number;
  max_drawdown: number;
  token_valid: boolean;
  equity_history: EquityPoint[];
  last_action: string;
  active_symbol?: string;
  last_ltp?: number;
  last_regime?: string;
  recent_execution: RecentExecution[];
  vol_regime?: string;
  risk_mult?: number;
  market: MarketStatus;
  per_symbol_status: Record<string, PerSymbolStatus>;
  live_snapshots: Record<string, LiveSnapshot>;
  last_proposed_signals?: Record<string, LiveSnapshot>;
  posture_snapshot?: PostureSnapshot;
  fo_mood?: FoMarketMoodSnapshot;
  options_legs?: OptionsLegsPayload;
  error?: string;
}

export interface StatusStreamPayload {
  timestamp: string;
  engine_ready?: boolean;
  per_symbol_status: Record<string, PerSymbolStatus>;
  live_snapshots: Record<string, LiveSnapshot>;
  last_action: string;
  recent_execution: RecentExecution[];
  last_proposed_signals: Record<string, LiveSnapshot>;
  posture_snapshot?: PostureSnapshot;
  fo_mood?: FoMarketMoodSnapshot;
  options_legs?: OptionsLegsPayload;
  global_params?: {
    vol_regime: string;
    risk_mult: number;
    equity_recent: EquityPoint[];
  };
}

export interface RiskConfig {
  loaded: boolean;
  capital: number;
  max_daily_loss_pct: number;
  max_daily_loss_rs: number;
  max_drawdown_pct: number;
  max_drawdown_rs: number;
  risk_per_trade_pct: number;
  max_trades_per_day: number;
  max_trades_per_symbol?: number;
  max_order_quantity: number;
  lot_size: number;
  lot_sizes?: Record<string, number>;
  max_lots: number;
  force_dry_run: boolean;
  daily_pnl: number;
  daily_loss: number;
  trades_today: number;
  current_drawdown_pct: number;
  state: string;
  trading_allowed?: boolean;
  trade_budget?: TradeBudgetSummary;
  error?: string;
}

export interface KiteStatus {
  api_key_configured: boolean;
  api_key_preview?: string;
  api_secret_configured: boolean;
  access_token_configured: boolean;
  connected: boolean;
  latency_ms?: number;
  user_id?: string;
  user_name?: string;
  broker?: string;
  error?: string;
  error_code?: string;
  needs_relogin?: boolean;
  cached?: boolean;
  stale?: boolean;
  timestamp: string;
}

export interface SystemInfo {
  version: string;
  market: MarketStatus;
  singletons_loaded: boolean;
  memory_runs: number | string;
  timestamp: string;
}

export interface OpsPreflightReport {
  ready: boolean;
  mode?: string;
  blockers?: string[];
  warnings?: string[];
  error?: string;
  status?: { healthy?: boolean; algo_id?: string; state?: string };
  compliance?: { passed?: boolean; automated_passed?: number; automated_total?: number };
  data_health?: { healthy?: boolean };
  wfo?: { any_passed?: boolean; all_passed?: boolean };
  timestamp?: string;
}

export interface BacktestJob {
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'queued';
  progress: number;
  stage?: string;
  started_at?: number;
  completed_at?: number;
  params?: Record<string, unknown>;
  result?: BacktestResult;
  error?: string;
  error_code?: string;
  type?: string;
  rows?: number;
  source?: string;
  contract?: string;
  contract_index?: number;
  contract_total?: number;
  cache_hit?: string;
  force_refresh?: boolean;
  gpu_available?: boolean;
  gpu_name?: string;
}

export interface BacktestResult {
  folds?: Array<{
    fold: number;
    test_return: number;
    test_pf: number;
    test_dd: number;
    trades: number;
    monte_carlo?: Record<string, unknown>;
  }>;
  avg_return?: number;
  avg_pf?: number;
  total_folds_run?: number;
  total_trades?: number;
  data_source?: string;
  data_bars?: number;
  data_warning?: string;
  research_mode_used?: boolean;
  statistical_power?: {
    total_trades: number;
    warning_level: string;
    message: string;
    recommendation: string;
  };
  monte_carlo?: Record<string, unknown>;
  cost_sensitivity_summary?: Record<string, number | string> & { note?: string };
  gpu_available?: boolean;
  gpu_used?: boolean;
  gpu_name?: string;
  gpu_device?: string;
}

export interface CachedDataset {
  filename: string;
  path?: string;
  symbol?: string;
  rows: number | string;
  file_from?: string;
  file_to?: string;
  actual_from?: string;
  actual_to?: string;
  size_kb?: number;
  mtime?: string;
  interval?: string;
  error?: string;
}

export interface DataHealthFile {
  filename?: string;
  path?: string;
  status: string;
  issues?: string[];
  rows?: number;
  actual_from?: string;
  actual_to?: string;
  days_old?: number;
  size_kb?: number;
}

export interface DataHealthReport {
  overall: 'healthy' | 'stale' | 'corrupt' | 'missing' | 'error';
  checked_at?: string;
  parquet_count?: number;
  parquet_ok?: number;
  parquet_stale?: number;
  parquet_corrupt?: number;
  latest_data_date?: string;
  recommendation?: string;
  parquet_files?: DataHealthFile[];
  kite_api?: {
    intervals?: string[];
    interval_max_days_per_request?: Record<string, number>;
    project_default_interval?: string;
    rate_limit_note?: string;
    docs_url?: string;
  };
  docker_note?: string;
  error?: string;
}

export interface MemoryInsights {
  error?: string;
  message?: string;
  total_runs?: number;
  documentation_notes?: string[];
  regime_stats?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RealFillsAnalysis {
  error?: string;
  error_code?: string;
  fills?: Array<{
    ts?: string;
    symbol?: string;
    qty?: number;
    price?: number;
    est_cost_round_turn_rs?: number;
  }>;
  summary?: {
    nifty_fills_analyzed?: number;
    est_total_cost_rs?: number;
  };
  documentation_notes?: string[];
  recent_orders_count?: number | string;
}