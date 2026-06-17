import { Link2, CheckCircle, XCircle, Loader2, LogIn, SlidersHorizontal } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { KiteStatus, OpsPreflightReport, SystemInfo, TradingControlsStatus } from '../api/types';
import PageShell from '../components/ui/PageShell';

const SHEET_MODES = [
  { value: 'off', label: 'Off' },
  { value: 'advisory', label: 'Advisory (log only)' },
  { value: 'filter', label: 'Filter (block mismatch)' },
  { value: 'confirm', label: 'Confirm (require alignment)' },
] as const;

export default function Settings() {
  const [kiteStatus, setKiteStatus] = useState<KiteStatus | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [preflight, setPreflight] = useState<OpsPreflightReport | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [loginMessage, setLoginMessage] = useState<string | null>(null);
  const [redirectUrl, setRedirectUrl] = useState<string | null>(null);
  const [tradingControls, setTradingControls] = useState<TradingControlsStatus | null>(null);
  const [controlsLoading, setControlsLoading] = useState(true);
  const [controlsError, setControlsError] = useState<string | null>(null);
  const [controlsSaving, setControlsSaving] = useState(false);
  const [controlsMsg, setControlsMsg] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = async () => {
    try {
      const [k, s] = await Promise.all([api.getKiteStatus(), api.getSystemInfo()]);
      setKiteStatus(k);
      setSystemInfo(s);
    } catch {
      setKiteStatus(null);
    }
  };

  const loadTradingControls = useCallback(async () => {
    setControlsLoading(true);
    setControlsError(null);
    try {
      const status = await api.getTradingControls();
      setTradingControls(status);
    } catch (e) {
      setTradingControls(null);
      setControlsError(
        e instanceof Error
          ? e.message
          : 'Trading controls API unavailable — restart run.py and rebuild frontend (npm run build)',
      );
    } finally {
      setControlsLoading(false);
    }
  }, []);

  const patchControl = async (patch: Record<string, boolean | string>) => {
    setControlsSaving(true);
    setControlsMsg(null);
    try {
      const res = await api.patchTradingControls(patch);
      if (res.success && res.status) {
        setTradingControls(res.status);
        setControlsMsg(res.message ?? 'Saved — effective immediately');
      } else {
        setControlsMsg(res.message ?? 'Update failed');
      }
    } catch (e) {
      setControlsMsg(e instanceof Error ? e.message : 'Update failed');
    } finally {
      setControlsSaving(false);
    }
  };

  const resetControls = async () => {
    if (!window.confirm('Clear portal overrides and use .env + strategy_config.yaml again?')) return;
    setControlsSaving(true);
    setControlsMsg(null);
    try {
      const res = await api.resetTradingControls();
      if (res.status) setTradingControls(res.status);
      setControlsMsg(res.message ?? 'Reset complete');
    } catch (e) {
      setControlsMsg(e instanceof Error ? e.message : 'Reset failed');
    } finally {
      setControlsSaving(false);
    }
  };

  const loadPreflight = async () => {
    setPreflightLoading(true);
    try {
      const report = await api.getOpsPreflight(1, false);
      setPreflight(report);
    } catch {
      setPreflight({ ready: false, error: 'Preflight API unavailable' });
    } finally {
      setPreflightLoading(false);
    }
  };

  useEffect(() => {
    load();
    loadPreflight();
    loadTradingControls();
    const id = setInterval(load, 30000);
    return () => {
      clearInterval(id);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadTradingControls]);

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const [health, kite] = await Promise.all([api.health(), api.getKiteStatus()]);
      setKiteStatus(kite);
      if (kite.connected) {
        setTestResult(`Connected as ${kite.user_name ?? kite.user_id} (${kite.latency_ms}ms) — backend ${health.status}`);
      } else {
        setTestResult(kite.error ?? 'Connection failed — use Auto Login below');
      }
    } catch (e) {
      setTestResult(`Backend unreachable: ${e}. Run python run.py --dev first.`);
    } finally {
      setTesting(false);
    }
  };

  const startAutoLogin = async () => {
    setLoggingIn(true);
    setLoginMessage('Opening Kite login in your browser...');
    setTestResult(null);
    try {
      const start = await api.startKiteLogin();
      setRedirectUrl(start.redirect_url_required ?? null);
      if (start.login_url) {
        window.open(start.login_url, '_blank', 'noopener,noreferrer');
      }
      setLoginMessage(start.message ?? 'Complete Zerodha login in the browser tab.');

      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getKiteLoginStatus();
          if (status.status === 'success') {
            setLoginMessage(`Logged in as ${status.user_name ?? 'user'} — token saved to .env`);
            setLoggingIn(false);
            if (pollRef.current) clearInterval(pollRef.current);
            await load();
          } else if (status.status === 'error') {
            setLoginMessage(status.error ?? status.message ?? 'Login failed');
            setLoggingIn(false);
            if (pollRef.current) clearInterval(pollRef.current);
          } else {
            setLoginMessage(status.message ?? 'Waiting for browser login...');
          }
        } catch {
          setLoginMessage('Lost connection to login status endpoint');
          setLoggingIn(false);
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }, 2000);
    } catch (e) {
      setLoginMessage(`Failed to start login: ${e}`);
      setLoggingIn(false);
    }
  };

  const loginTone = loginMessage?.includes('Logged in')
    ? 'ok'
    : loggingIn
      ? 'info'
      : 'error';
  const testTone = testResult?.includes('Connected') ? 'ok' : 'error';

  return (
    <PageShell
      className="page-shell--settings"
      subtitle="Broker connectivity and system diagnostics."
    >
      <div className="bento-grid settings-grid">
        <div className="bento-tile bento-tile--auto settings-tile-trading">
          <div className="flex flex-col gap-3 flex-1 min-h-0">
            <h3 className="tile-title m-0">
              <SlidersHorizontal size={18} /> Trading Controls
            </h3>
            <p className="text-sm text-muted m-0">
              Toggle engines from the portal — saves to <code>data/trading_controls.json</code> and applies
              immediately (no <code>run.py</code> restart). Live capital still requires{' '}
              <code>LIVE_TRADING_CONFIRMED</code> in <code>.env</code>.
            </p>

            {controlsError && (
              <p className="message-banner message-banner--error m-0">{controlsError}</p>
            )}

            {controlsLoading && !tradingControls?.effective && (
              <p className="text-sm text-muted m-0 flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Loading trading controls…
              </p>
            )}

            <div className="trading-controls-grid">
              <label className="trading-control-row">
                <span>
                  <span className="trading-control-label">Options algo (Iron Condor)</span>
                  <span className="trading-control-hint">Automated 4-leg structures via RiskGatekeeper</span>
                </span>
                <input
                  type="checkbox"
                  className="trading-control-toggle"
                  checked={!!tradingControls?.effective?.options_trading_enabled}
                  disabled={controlsSaving || controlsLoading || !!controlsError}
                  onChange={(e) => patchControl({ options_trading_enabled: e.target.checked })}
                />
              </label>

              <label className="trading-control-row">
                <span>
                  <span className="trading-control-label">Options EOD flatten</span>
                  <span className="trading-control-hint">Auto-close open structures before session end</span>
                </span>
                <input
                  type="checkbox"
                  className="trading-control-toggle"
                  checked={tradingControls?.effective?.options_eod_flatten_enabled ?? true}
                  disabled={controlsSaving || controlsLoading || !!controlsError}
                  onChange={(e) => patchControl({ options_eod_flatten_enabled: e.target.checked })}
                />
              </label>

              <label className="trading-control-row">
                <span>
                  <span className="trading-control-label">Futures engine (3-index)</span>
                  <span className="trading-control-hint">NIFTY / BANKNIFTY / SENSEX breakout</span>
                </span>
                <input
                  type="checkbox"
                  className="trading-control-toggle"
                  checked={!!tradingControls?.effective?.futures_trading_enabled}
                  disabled={controlsSaving || controlsLoading || !!controlsError}
                  onChange={(e) => patchControl({ futures_trading_enabled: e.target.checked })}
                />
              </label>

              <label className="trading-control-row">
                <span>
                  <span className="trading-control-label">Options sheet → algo gate</span>
                  <span className="trading-control-hint">Manual 6-leg sheet bias integration</span>
                </span>
                <input
                  type="checkbox"
                  className="trading-control-toggle"
                  checked={!!tradingControls?.effective?.external_signals_enabled}
                  disabled={controlsSaving || controlsLoading || !!controlsError}
                  onChange={(e) => patchControl({ external_signals_enabled: e.target.checked })}
                />
              </label>

              <div className="trading-control-row trading-control-row--select">
                <span>
                  <span className="trading-control-label">Sheet gate mode</span>
                  <span className="trading-control-hint">How sheet CE/PE bias affects futures entries</span>
                </span>
                <select
                  className="trading-control-select"
                  value={tradingControls?.effective?.external_signals_mode ?? 'filter'}
                  disabled={
                    controlsSaving
                    || controlsLoading
                    || !!controlsError
                    || !tradingControls?.effective?.external_signals_enabled
                  }
                  onChange={(e) => patchControl({ external_signals_mode: e.target.value })}
                >
                  {SHEET_MODES.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="page-kv-list text-sm">
              <div className="data-row">
                <span className="text-muted">Paper mode</span>
                <span className={tradingControls?.effective?.force_dry_run ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
                  {tradingControls?.effective?.force_dry_run ? '✓ ON (safe)' : '✗ LIVE'}
                </span>
              </div>
              <div className="data-row">
                <span className="text-muted">Portal override file</span>
                <span className="font-mono text-xs truncate">
                  {tradingControls?.persisted?.file ?? '—'}
                </span>
              </div>
              {tradingControls?.persisted?.updated_at && (
                <div className="data-row">
                  <span className="text-muted">Last saved</span>
                  <span className="font-mono text-xs">{tradingControls.persisted.updated_at}</span>
                </div>
              )}
            </div>

            {controlsMsg && (
              <p className="message-banner message-banner--info m-0">{controlsMsg}</p>
            )}

            <div className="header-actions mt-auto pt-1">
              <button className="btn btn-secondary" onClick={loadTradingControls} disabled={controlsSaving}>
                Refresh
              </button>
              <button className="btn btn-secondary" onClick={resetControls} disabled={controlsSaving}>
                Reset to file defaults
              </button>
            </div>
          </div>
        </div>

        <div className="bento-tile bento-tile--auto settings-tile-kite">
          <div className="flex flex-col gap-3 flex-1 min-h-0">
            <h3 className="tile-title m-0">
              <Link2 size={18} /> Zerodha Kite Integration
            </h3>

            <div className="status-banner">
              {kiteStatus?.connected ? (
                <CheckCircle size={20} className="text-profit flex-shrink-0" />
              ) : (
                <XCircle size={20} className="text-loss flex-shrink-0" />
              )}
              <div className="min-w-0">
                <div className="text-sm font-semibold">
                  {kiteStatus?.connected ? `Connected — ${kiteStatus.user_name ?? kiteStatus.user_id}` : 'Not Connected'}
                </div>
                <div className="text-xs text-muted truncate">
                  {kiteStatus?.api_key_preview ? `API Key: ${kiteStatus.api_key_preview}` : 'No API key in .env'}
                  {kiteStatus?.latency_ms ? ` · ${kiteStatus.latency_ms}ms` : ''}
                </div>
              </div>
            </div>

            <div className="stat-grid">
              <div className="stat-cell">
                <span className="stat-cell-label">API Key</span>
                <span className="stat-cell-value">{kiteStatus?.api_key_configured ? '✓ Configured' : '✗ Missing'}</span>
              </div>
              <div className="stat-cell">
                <span className="stat-cell-label">Access Token</span>
                <span className="stat-cell-value">{kiteStatus?.access_token_configured ? '✓ Set' : '✗ Missing'}</span>
              </div>
              <div className="stat-cell">
                <span className="stat-cell-label">API Secret</span>
                <span className="stat-cell-value">{kiteStatus?.api_secret_configured ? '✓ Set' : '✗ Missing'}</span>
              </div>
              <div className="stat-cell">
                <span className="stat-cell-label">Broker</span>
                <span className="stat-cell-value">{kiteStatus?.broker ?? '—'}</span>
              </div>
            </div>

            <div className="hint-box">
              <strong className="text-main">One-time setup:</strong> In the{' '}
              <a href="https://developers.kite.trade/apps" target="_blank" rel="noreferrer" className="text-brand hover:underline">
                Kite developer console
              </a>
              , set Redirect URL to:
              <div className="font-mono mt-2 text-brand">
                {redirectUrl ?? 'http://127.0.0.1:8765/callback'}
              </div>
              Tokens expire daily at 6 AM IST — use Auto Login each morning (browser + 2FA still required).
            </div>

            {loginMessage && (
              <p className={`message-banner message-banner--${loginTone}`}>{loginMessage}</p>
            )}
            {testResult && (
              <p className={`message-banner message-banner--${testTone}`}>{testResult}</p>
            )}

            <div className="header-actions mt-auto pt-1">
              <button className="btn btn-primary" onClick={startAutoLogin} disabled={loggingIn}>
                {loggingIn ? (
                  <>
                    <Loader2 size={14} className="animate-spin" /> Waiting for login...
                  </>
                ) : (
                  <>
                    <LogIn size={14} /> Auto Login
                  </>
                )}
              </button>
              <button className="btn btn-secondary" onClick={testConnection} disabled={testing}>
                {testing ? 'Testing...' : 'Test Connection'}
              </button>
              <button className="btn btn-secondary" onClick={load}>Refresh</button>
            </div>
          </div>
        </div>

        <div className="bento-tile bento-tile--auto settings-tile-preflight">
          <div className="flex flex-col gap-3 flex-1 min-h-0">
            <h3 className="tile-title m-0">Morning Preflight</h3>
            <p className="text-sm text-muted m-0">
              Same checks as <code>python scripts/algo_lab_ops.py preflight</code> — status, compliance, data-health, WFO.
            </p>
            <div className="page-kv-list text-sm">
              <div className="data-row">
                <span className="text-muted">Ready</span>
                <span className={preflight?.ready ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
                  {preflightLoading ? '…' : preflight?.ready ? '✓ Yes' : '✗ No'}
                </span>
              </div>
              <div className="data-row">
                <span className="text-muted">Mode</span>
                <span className="font-mono">{preflight?.mode ?? '—'}</span>
              </div>
              <div className="data-row">
                <span className="text-muted">Compliance</span>
                <span className="font-mono">
                  {preflight?.compliance
                    ? `${preflight.compliance.automated_passed ?? 0}/${preflight.compliance.automated_total ?? 0}`
                    : '—'}
                </span>
              </div>
            </div>
            {(preflight?.blockers?.length ?? 0) > 0 && (
              <div className="message-banner message-banner--error">
                <strong>Blockers:</strong> {preflight?.blockers?.slice(0, 4).join(' · ')}
              </div>
            )}
            {(preflight?.warnings?.length ?? 0) > 0 && (
              <div className="message-banner message-banner--info">
                <strong>Warnings:</strong> {preflight?.warnings?.slice(0, 4).join(' · ')}
              </div>
            )}
            {preflight?.error && (
              <div className="message-banner message-banner--error">{preflight.error}</div>
            )}
            <div className="header-actions mt-auto pt-1">
              <button className="btn btn-secondary" onClick={loadPreflight} disabled={preflightLoading}>
                {preflightLoading ? 'Running…' : 'Run Preflight'}
              </button>
            </div>
            <p className="text-xs text-muted m-0">
              API: <code>GET /api/ops/preflight</code>, <code>/api/ops/status</code>, <code>/api/ops/compliance</code>
            </p>
          </div>
        </div>

        <div className="bento-tile bento-tile--auto settings-tile-system">
          <div className="flex flex-col gap-3">
            <h3 className="tile-title m-0">System Diagnostics</h3>
            <div className="page-kv-list text-sm">
              <div className="data-row">
                <span className="text-muted">Backend Version</span>
                <span className="font-mono">{systemInfo?.version ?? '—'}</span>
              </div>
              <div className="data-row">
                <span className="text-muted">Engine Loaded</span>
                <span>{systemInfo?.singletons_loaded ? '✓ Yes' : '✗ No'}</span>
              </div>
              <div className="data-row">
                <span className="text-muted">Market Session</span>
                <span className="font-mono">{systemInfo?.market?.session_status ?? '—'}</span>
              </div>
              <div className="data-row">
                <span className="text-muted">Backtest Memory Runs</span>
                <span className="font-mono">{systemInfo?.memory_runs ?? '—'}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="bento-tile bento-tile--auto settings-tile-cli">
          <div className="flex flex-col gap-3">
            <h3 className="tile-title m-0">CLI Alternatives</h3>
            <div className="text-sm text-muted leading-relaxed flex flex-col gap-1">
              <p className="m-0"><code>python generate_token.py</code> — auto-login from terminal</p>
              <p className="m-0"><code>python run.py --ensure-token</code> — validate token before trading, auto-login if expired</p>
              <p className="m-0"><code>python generate_token.py --validate</code> — check current token only</p>
            </div>
          </div>
        </div>
      </div>
    </PageShell>
  );
}