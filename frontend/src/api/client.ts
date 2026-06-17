import { fetchWithStaleCache } from './staleCache';
import { todayIst } from '../utils/dates';
import type {
  AgentInsights,
  TradingJournalEntry,
  TradingJournalSummary,
  BacktestJob,
  CachedDataset,
  DataHealthReport,
  ExternalJournalRow,
  ExternalSignalsSheet,
  OptionsAlgoCloseResult,
  OptionsAlgoStatus,
  OptionsDeskTickers,
  OptionsLegsPayload,
  SheetVsAlgoComparison,
  FoMarketMoodSnapshot,
  KiteStatus,
  MemoryInsights,
  RealFillsAnalysis,
  RiskConfig,
  OpsPreflightReport,
  SystemInfo,
  SystemStatus,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

const DEFAULT_TIMEOUT_MS = 8000;
/** Kite-backed sheet / options endpoints — batched but can spike on cold cache. */
const KITE_SLOW_TIMEOUT_MS = 20000;

async function request<T>(path: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init?.headers ?? {}),
      },
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new ApiError(body || res.statusText, res.status);
    }

    return res.json() as Promise<T>;
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(
        `Request timed out after ${timeoutMs}ms — engine may be slow or unreachable on :8050`,
      );
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

export const api = {
  health: () => request<{ status: string; timestamp: string; engine_ready?: boolean }>('/health', undefined, 15000),

  getStatusQuick: () => request<SystemStatus>('/api/status/quick'),

  getStatus: () => request<SystemStatus>('/api/status'),

  getMarketStatus: () => request<Record<string, unknown>>('/api/market/status'),

  getFoMarketMood: () => request<FoMarketMoodSnapshot>('/api/market/fo-mood'),

  getRiskConfig: () => request<RiskConfig>('/api/risk/config'),

  getKiteStatus: (opts?: { quick?: boolean; skipCache?: boolean }) => {
    const quick = opts?.quick ?? false;
    const path = `/api/kite/status${quick ? '?quick=true' : ''}`;
    const cacheKey = `kite-status:${quick ? 'quick' : 'full'}`;
    const fetcher = () => request<KiteStatus>(path, undefined, KITE_SLOW_TIMEOUT_MS);
    if (opts?.skipCache) return fetcher();
    return fetchWithStaleCache(cacheKey, 30_000, fetcher);
  },

  startKiteLogin: () =>
    request<{
      status: string;
      message?: string;
      login_url?: string;
      redirect_url_required?: string;
      setup_note?: string;
    }>('/api/kite/login/start', { method: 'POST' }),

  getKiteLoginStatus: () =>
    request<{
      status: string;
      message?: string;
      error?: string;
      user_name?: string;
      login_url?: string;
      redirect_url_required?: string;
    }>('/api/kite/login/status'),

  emergencyHalt: () =>
    request<{
      status: string;
      state: string;
      reason: string;
      positions_closed: Array<Record<string, unknown>>;
      trading_allowed: boolean;
    }>('/api/emergency/halt', { method: 'POST' }),

  getMemoryInsights: (regime?: string) =>
    request<MemoryInsights>(
      `/api/memory/insights${regime ? `?regime=${regime}` : ''}`
    ),

  getAgentInsights: (refresh = false) =>
    request<AgentInsights>(`/api/agent/insights${refresh ? '?refresh=true' : ''}`),

  getJournal: (date?: string) =>
    request<{ journal: TradingJournalEntry | null; date_ist?: string; error?: string }>(
      `/api/journal${date ? `?date=${date}` : ''}`
    ),

  listJournals: (limit = 30) =>
    request<{ journals: TradingJournalSummary[]; error?: string }>(
      `/api/journal/list?limit=${limit}`
    ),

  addJournalNote: (note: string, date_ist?: string) =>
    request<{ ok: boolean; journal?: TradingJournalEntry; error?: string }>(
      '/api/journal/note',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note, date_ist }),
      }
    ),

  buildJournal: (date_ist?: string) =>
    request<{ ok: boolean; journal?: TradingJournalEntry; path?: string; error?: string }>(
      `/api/journal/build${date_ist ? `?date=${date_ist}` : ''}`,
      { method: 'POST' }
    ),

  syncHolidays: () =>
    request<{
      ok: boolean;
      holiday_count?: number;
      calendar_total?: number;
      new_from_file?: number;
      errors?: string[];
      error?: string;
    }>('/api/data/holidays/sync', { method: 'POST' }),

  getHolidays: () =>
    request<{
      holiday_count?: number;
      synced_at?: string;
      holidays?: Array<{ date: string; description?: string; segment?: string }>;
      calendar_total?: number;
      errors?: string[];
    }>('/api/data/holidays'),

  getEodAudit: (date?: string) =>
    request<{ report?: Record<string, unknown>; error?: string }>(
      `/api/data/eod-audit${date ? `?date=${date}` : ''}`
    ),

  getRealFillsAnalysis: (limit = 40) =>
    request<RealFillsAnalysis>(`/api/kite/real_fills_analysis?limit=${limit}`),

  getSystemInfo: () => request<SystemInfo>('/api/system/info'),

  getTradingControls: () => request<import('./types').TradingControlsStatus>('/api/settings/trading'),

  patchTradingControls: (patch: Record<string, boolean | string>) =>
    request<import('./types').TradingControlsPatchResult>('/api/settings/trading', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),

  resetTradingControls: () =>
    request<import('./types').TradingControlsPatchResult>('/api/settings/trading/reset', {
      method: 'POST',
    }),

  getOpsPreflight: (days = 1, skipToken = false) =>
    request<OpsPreflightReport>(
      `/api/ops/preflight?days=${days}${skipToken ? '&skip_token=true' : ''}`
    ),

  getOpsStatus: (skipToken = false) =>
    request<Record<string, unknown>>(
      `/api/ops/status${skipToken ? '?skip_token=true' : ''}`
    ),

  getOpsCompliance: () => request<Record<string, unknown>>('/api/ops/compliance'),

  getTrades: (limit = 50, date?: string) => {
    const tradeDate = date ?? todayIst();
    return request<{ trades: Array<Record<string, unknown>>; error?: string; date?: string }>(
      `/api/trades?limit=${limit}&date=${encodeURIComponent(tradeDate)}`,
    );
  },

  getExternalSignals: (date?: string, opts?: { withPnl?: boolean }) => {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    if (opts?.withPnl === false) params.set('with_pnl', 'false');
    const qs = params.toString();
    return request<{ sheet: ExternalSignalsSheet; display_names: Record<string, string> }>(
      `/api/external-signals${qs ? `?${qs}` : ''}`,
      undefined,
      opts?.withPnl === false ? DEFAULT_TIMEOUT_MS : KITE_SLOW_TIMEOUT_MS,
    );
  },

  getExternalSignalDates: () =>
    request<{ dates: string[] }>('/api/external-signals/dates'),

  saveExternalSignals: (sheet: ExternalSignalsSheet) =>
    request<{ ok: boolean; sheet: ExternalSignalsSheet; journal_rows?: ExternalJournalRow[] }>(
      '/api/external-signals',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sheet }),
      },
    ),

  deleteExternalSignals: (date: string) =>
    request<{ ok: boolean; deleted: boolean; date: string; sheet: ExternalSignalsSheet }>(
      `/api/external-signals/delete?date=${encodeURIComponent(date)}`,
      { method: 'POST' },
    ),

  getExternalSignalPremiums: (date?: string) =>
    request<{
      sheet_date: string;
      premiums: Record<string, unknown>;
      sheet?: ExternalSignalsSheet;
      pnl_summary?: import('./types').OptionsPnlSummary;
    }>(`/api/external-signals/premiums${date ? `?date=${date}` : ''}`, undefined, KITE_SLOW_TIMEOUT_MS),

  evaluateExternalSignals: (date?: string) =>
    request<{
      ok: boolean;
      sheet: ExternalSignalsSheet;
      premiums: Record<string, unknown>;
      journal_rows: ExternalJournalRow[];
      pnl_summary?: import('./types').OptionsPnlSummary;
      error?: string;
    }>(
      `/api/external-signals/evaluate${date ? `?date=${date}` : ''}`,
      { method: 'POST' },
      KITE_SLOW_TIMEOUT_MS,
    ),

  getExternalSignalJournal: (limit = 90, date?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (date) params.set('date', date);
    return request<{ rows: ExternalJournalRow[]; dates: string[]; filtered_date?: string | null }>(
      `/api/external-signals/journal?${params}`
    );
  },

  getSheetVsAlgoComparison: (date?: string) =>
    request<SheetVsAlgoComparison>(
      `/api/external-signals/comparison${date ? `?date=${date}` : ''}`,
      undefined,
      KITE_SLOW_TIMEOUT_MS,
    ),

  getOptionsLegsLive: (date?: string) =>
    request<OptionsLegsPayload>(
      `/api/options-legs/live${date ? `?date=${date}` : ''}`,
      undefined,
      KITE_SLOW_TIMEOUT_MS,
    ),

  getOptionsAlgoStatus: (opts?: { fast?: boolean; skipCache?: boolean }) => {
    const fast = opts?.fast ?? false;
    const path = `/api/options/algo/status${fast ? '?fast=true' : ''}`;
    const cacheKey = `options-algo-status:${fast ? 'fast' : 'full'}`;
    const ttl = fast ? 12_000 : 20_000;
    const fetcher = () => request<OptionsAlgoStatus>(path, undefined, KITE_SLOW_TIMEOUT_MS);
    if (opts?.skipCache) return fetcher();
    return fetchWithStaleCache(cacheKey, ttl, fetcher);
  },

  getOptionsDeskTickers: (opts?: { skipCache?: boolean }) => {
    const cacheKey = 'options-desk-tickers';
    const fetcher = () =>
      request<OptionsDeskTickers>('/api/options/desk/tickers', undefined, KITE_SLOW_TIMEOUT_MS);
    if (opts?.skipCache) return fetcher();
    return fetchWithStaleCache(cacheKey, 15_000, fetcher);
  },

  getTradesCached: (limit = 40, opts?: { skipCache?: boolean; date?: string }) => {
    const tradeDate = opts?.date ?? todayIst();
    const cacheKey = `trades:${limit}:${tradeDate}`;
    const fetcher = () =>
      request<{ trades: Array<Record<string, unknown>>; error?: string; date?: string }>(
        `/api/trades?limit=${limit}&date=${encodeURIComponent(tradeDate)}`,
      );
    if (opts?.skipCache) return fetcher();
    return fetchWithStaleCache(cacheKey, 12_000, fetcher);
  },

  closeOptionsAlgoStructure: (structureId: string) =>
    request<OptionsAlgoCloseResult>(`/api/options/algo/close/${encodeURIComponent(structureId)}`, {
      method: 'POST',
    }),

  getCachedDatasets: () =>
    request<{ datasets: CachedDataset[]; count: number; error?: string }>(
      '/api/data/cached_datasets'
    ),

  getDataHealth: (staleDays = 5) =>
    request<DataHealthReport>(`/api/data/health?stale_days=${staleDays}`),

  getMemoryReport: () => request<Record<string, unknown>>('/api/memory/report'),

  getBacktestResult: (jobId: string) => request<BacktestJob>(`/api/backtest/result/${jobId}`),

  runBacktest: (params: {
    months: number;
    folds: number;
    risk_low: number;
    risk_high: number;
    max_trades: number;
    vol_strict: number;
    research_mode?: boolean;
    cost_multiplier?: number;
    entry_on_next_bar?: boolean;
    quick_mode?: boolean;
    use_real_data?: boolean;
    force_refresh?: boolean;
  }) => {
    const form = new FormData();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) form.append(k, String(v));
    });
    return request<{ job_id: string; status: string }>('/api/backtest/run', {
      method: 'POST',
      body: form,
    });
  },

  cancelBacktest: (jobId: string) =>
    request<{ status: string; job_id: string }>(`/api/backtest/cancel/${jobId}`, {
      method: 'POST',
    }),

  fetchHistoricalData: (params: {
    months: number;
    use_local_only?: boolean;
    force_refresh?: boolean;
  }) => {
    const form = new FormData();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) form.append(k, String(v));
    });
    return request<{ job_id: string }>('/api/data/fetch', { method: 'POST', body: form });
  },
};

export { ApiError };