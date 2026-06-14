import { Link2, CheckCircle, XCircle, Loader2, LogIn } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { KiteStatus, SystemInfo } from '../api/types';
import PageShell from '../components/ui/PageShell';

export default function Settings() {
  const [kiteStatus, setKiteStatus] = useState<KiteStatus | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [testing, setTesting] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [loginMessage, setLoginMessage] = useState<string | null>(null);
  const [redirectUrl, setRedirectUrl] = useState<string | null>(null);
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

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => {
      clearInterval(id);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

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
      maxWidth={720}
      subtitle="Broker connectivity and system diagnostics."
    >
      <div className="tile-stack">
        <div className="bento-tile">
          <h3 className="tile-title">
            <Link2 size={18} /> Zerodha Kite Integration
          </h3>

          <div className="flex flex-col gap-4">
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

            <div className="header-actions">
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

        <div className="bento-tile">
          <h3 className="tile-title">CLI Alternatives</h3>
          <div className="text-sm text-muted leading-relaxed flex flex-col gap-1">
            <p className="m-0"><code>python generate_token.py</code> — auto-login from terminal</p>
            <p className="m-0"><code>python run.py --ensure-token</code> — validate token before trading, auto-login if expired</p>
            <p className="m-0"><code>python generate_token.py --validate</code> — check current token only</p>
          </div>
        </div>

        <div className="bento-tile">
          <h3 className="tile-title">System Diagnostics</h3>
          <div className="flex flex-col gap-3 text-sm">
            <div className="data-row py-2">
              <span className="text-muted">Backend Version</span>
              <span className="font-mono">{systemInfo?.version ?? '—'}</span>
            </div>
            <div className="data-row py-2">
              <span className="text-muted">Engine Loaded</span>
              <span>{systemInfo?.singletons_loaded ? '✓ Yes' : '✗ No'}</span>
            </div>
            <div className="data-row py-2">
              <span className="text-muted">Market Session</span>
              <span className="font-mono">{systemInfo?.market?.session_status ?? '—'}</span>
            </div>
            <div className="data-row py-2">
              <span className="text-muted">Backtest Memory Runs</span>
              <span className="font-mono">{systemInfo?.memory_runs ?? '—'}</span>
            </div>
          </div>
        </div>
      </div>
    </PageShell>
  );
}