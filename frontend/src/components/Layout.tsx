import { Outlet, NavLink, useLocation } from 'react-router-dom';
import { LayoutDashboard, Wand2, ShieldAlert, History, Settings, Shield, Power, Activity, Cpu, FileSpreadsheet, BookOpen, Brain } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import EngineBanner from './EngineBanner';
import { useStatusStream } from '../hooks/useStatusStream';
import { deriveEngineConnectivity, isEngineOnline } from '../hooks/useEngineConnectivity';
import { formatINR } from '../utils/format';
import { formatIstClock } from '../utils/dates';
import { computeDailyPnlBreakdown } from '../utils/foCosts';
import type { EngineConnectivity } from '../hooks/useEngineConnectivity';
import type { KiteStatus, StatusStreamPayload, SystemStatus } from '../api/types';

export interface LayoutOutletContext {
  status: SystemStatus | null;
  stream: StatusStreamPayload | null;
  connectivity: EngineConnectivity;
  engineOnline: boolean;
}

const HEALTH_POLL_MS = 12000;
const QUICK_STATUS_POLL_MS_OPEN = 15000;
const QUICK_STATUS_POLL_MS_CLOSED = 45000;
const FULL_STATUS_POLL_MS_OPEN = 90000;
const FULL_STATUS_POLL_MS_CLOSED = 180000;

export default function Layout() {
  const location = useLocation();
  const [isKilled, setIsKilled] = useState(false);
  const [showKillModal, setShowKillModal] = useState(false);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [kiteStatus, setKiteStatus] = useState<KiteStatus | null>(null);
  const [apiReachable, setApiReachable] = useState(false);
  const [healthEngineReady, setHealthEngineReady] = useState(false);
  const [statusLoading, setStatusLoading] = useState(true);
  const [toast, setToast] = useState<{ message: string; tone: 'ok' | 'warn' | 'error' } | null>(null);
  const [istClock, setIstClock] = useState(() => formatIstClock());
  const { data: stream, connected: streamConnected, error: streamError } = useStatusStream(!isKilled);

  useEffect(() => {
    const tick = () => setIstClock(formatIstClock());
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(id);
  }, [toast]);

  const navItems = [
    { path: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { path: '/options-sheet', label: 'Options Sheet', icon: FileSpreadsheet },
    { path: '/strategies', label: 'Strategies', icon: Wand2 },
    { path: '/risk', label: 'Risk Guard', icon: ShieldAlert },
    { path: '/backtest', label: 'Backtest', icon: History },
    { path: '/insights', label: 'Insights', icon: Brain },
    { path: '/journal', label: 'Journal', icon: BookOpen },
    { path: '/settings', label: 'Settings', icon: Settings },
  ];

  const pageTitles: Record<string, string> = {
    '/options-sheet': 'Daily Options Sheet',
    '/journal': 'Trading Journal',
  };

  const pageTitle =
    pageTitles[location.pathname]
    ?? navItems.find((item) => item.path === location.pathname)?.label
    ?? 'Overview';

  const [marketOpenHint, setMarketOpenHint] = useState<boolean | undefined>(undefined);
  const healthFailStreakRef = useRef(0);
  const HEALTH_FAIL_BEFORE_DOWN = 3;

  useEffect(() => {
    let active = true;

    const pollHealth = async (retries = 0) => {
      const maxAttempts = retries > 0 ? retries : 1;
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        try {
          const h = await api.health();
          if (!active) return;
          healthFailStreakRef.current = 0;
          setApiReachable(h.status === 'ok');
          setHealthEngineReady(Boolean(h.engine_ready));
          return;
        } catch {
          if (!active) return;
          if (attempt < maxAttempts - 1) {
            await new Promise((resolve) => window.setTimeout(resolve, 1500));
            continue;
          }
          healthFailStreakRef.current += 1;
          if (healthFailStreakRef.current >= HEALTH_FAIL_BEFORE_DOWN) {
            setApiReachable(false);
            setHealthEngineReady(false);
          }
        }
      }
    };

    const pollStatusQuick = async () => {
      try {
        const s = await api.getStatusQuick();
        if (!active) return;
        setStatus(s);
        setMarketOpenHint(s.market?.is_market_open);
        setStatusLoading(false);
      } catch {
        if (!active) return;
        // Keep last good status; health alone proves API is up.
      }
    };

    const pollStatusFull = async () => {
      try {
        const s = await api.getStatus();
        if (!active) return;
        setStatus(s);
        setMarketOpenHint(s.market?.is_market_open);
        setStatusLoading(false);
      } catch {
        // Full status is best-effort; quick path keeps UI alive.
      }
    };

    const pollKite = () => {
      api.getKiteStatus({ quick: true })
        .then((k) => { if (active) setKiteStatus({ ...k, stale: false }); })
        .catch(() => {
          if (!active) return;
          setKiteStatus((prev) => (
            prev?.connected
              ? { ...prev, stale: true }
              : {
                  api_key_configured: prev?.api_key_configured ?? false,
                  api_secret_configured: prev?.api_secret_configured ?? false,
                  access_token_configured: prev?.access_token_configured ?? false,
                  connected: false,
                  error_code: 'KITE_TIMEOUT',
                  error: 'Kite status slow — will retry (engine may be busy)',
                  stale: true,
                  timestamp: new Date().toISOString(),
                }
          ));
        });
    };

    const bootstrap = async () => {
      await pollHealth(8);
      if (!active) return;
      try {
        await pollStatusQuick();
      } finally {
        if (active) setStatusLoading(false);
      }
      pollKite();
      void pollStatusFull();
    };

    bootstrap();
    const healthId = window.setInterval(pollHealth, HEALTH_POLL_MS);
    const kiteId = window.setInterval(pollKite, 45000);

    return () => {
      active = false;
      window.clearInterval(healthId);
      window.clearInterval(kiteId);
    };
  }, []);

  useEffect(() => {
    let active = true;
    const quickMs = marketOpenHint ? QUICK_STATUS_POLL_MS_OPEN : QUICK_STATUS_POLL_MS_CLOSED;
    const fullMs = marketOpenHint ? FULL_STATUS_POLL_MS_OPEN : FULL_STATUS_POLL_MS_CLOSED;

    const pollStatusQuick = async () => {
      try {
        const s = await api.getStatusQuick();
        if (!active) return;
        setStatus(s);
        setMarketOpenHint(s.market?.is_market_open);
        setStatusLoading(false);
      } catch {
        if (!active) return;
      }
    };

    const pollStatusFull = async () => {
      try {
        const s = await api.getStatus();
        if (!active) return;
        setStatus(s);
        setMarketOpenHint(s.market?.is_market_open);
        setStatusLoading(false);
      } catch {
        // Full status is best-effort.
      }
    };

    void pollStatusQuick();
    const quickId = window.setInterval(pollStatusQuick, quickMs);
    const fullId = window.setInterval(pollStatusFull, fullMs);

    return () => {
      active = false;
      window.clearInterval(quickId);
      window.clearInterval(fullId);
    };
  }, [marketOpenHint]);

  const liveStatus = status ? {
    ...status,
    ...(stream ? {
      live_snapshots: { ...status.live_snapshots, ...stream.live_snapshots },
      per_symbol_status: { ...status.per_symbol_status, ...stream.per_symbol_status },
      recent_execution: stream.recent_execution?.length ? stream.recent_execution : status.recent_execution,
      fo_mood: stream.fo_mood ?? status.fo_mood,
      last_action: stream.last_action || status.last_action,
      engine_ready: stream.engine_ready ?? status.engine_ready,
    } : {}),
  } : null;

  const connectivity = useMemo(
    () => deriveEngineConnectivity(
      apiReachable,
      healthEngineReady,
      statusLoading,
      liveStatus,
      streamConnected,
    ),
    [apiReachable, healthEngineReady, statusLoading, liveStatus, streamConnected],
  );

  const engineOnline = isEngineOnline(connectivity);
  const isPaper = liveStatus?.mode === 'PAPER';
  const pnlBreakdown = computeDailyPnlBreakdown(
    liveStatus?.daily_pnl ?? 0,
    liveStatus?.options_mtm,
    liveStatus?.per_symbol_status ?? {},
  );
  const dailyPnl = pnlBreakdown.combinedNet;
  const marketOpen = liveStatus?.market?.is_market_open;
  const sessionStatus = liveStatus?.market?.session_status ?? (apiReachable ? 'CONNECTING' : 'UNKNOWN');

  const handleKill = async () => {
    setShowKillModal(false);
    try {
      const result = await api.emergencyHalt();
      setIsKilled(true);
      console.info('[KILL SWITCH]', result);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      setToast({
        message: `Kill switch failed: ${msg}. Is python run.py running?`,
        tone: 'error',
      });
    }
  };

  return (
    <div className="flex h-screen w-screen">

      <nav className="app-sidebar flex flex-col border-r border-dim flex-shrink-0">
        <div className="flex items-center gap-3 p-6">
          <div
            className="flex items-center justify-center text-white rounded-md shadow-[0_0_15px_var(--brand-glow)]"
            style={{
              width: '36px',
              height: '36px',
              background: 'linear-gradient(135deg, var(--brand-primary), var(--brand-secondary))',
            }}
          >
            <Shield size={20} strokeWidth={2.25} />
          </div>
          <div className="text-lg font-semibold tracking-[0.14em] uppercase text-main">
            Aegis
          </div>
        </div>

        <div className="flex flex-col gap-1 px-4 flex-1 mt-4">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                `flex items-center gap-3 p-3 rounded-md text-sm font-medium transition-all ${
                  isActive
                    ? 'text-brand bg-[rgba(99,102,241,0.1)] shadow-[inset_2px_0_0_var(--brand-primary)]'
                    : 'text-muted hover:text-main hover:bg-[rgba(255,255,255,0.02)]'
                }`
              }
            >
              <item.icon size={18} />
              {item.label}
            </NavLink>
          ))}
        </div>

        <div className="flex items-center gap-3 p-6 border-t border-dim mt-auto">
          <div className="rounded-full" style={{
            width: '10px', height: '10px',
            backgroundColor: kiteStatus?.connected ? 'var(--intent-profit)' : 'var(--intent-loss)',
            boxShadow: kiteStatus?.connected ? '0 0 10px var(--intent-profit)' : '0 0 10px var(--intent-loss)',
          }} />
          <div className="flex flex-col">
            <span className="text-sm font-semibold">Kite API</span>
            <span className="text-xs text-muted font-mono tracking-wide">
              {kiteStatus?.connected
                ? `Connected • ${kiteStatus.latency_ms ?? 'live'}${kiteStatus.stale ? ' (stale)' : ''}`
                : kiteStatus?.error_code === 'KITE_TOKEN_EXPIRED'
                  ? 'Token expired — Settings → Auto Login'
                  : kiteStatus?.error_code === 'KITE_CREDENTIALS_MISSING'
                    ? 'Missing API key/token in .env'
                    : kiteStatus?.stale
                      ? 'Checking Kite…'
                      : kiteStatus?.error_code ?? 'Not connected'}
            </span>
          </div>
        </div>
      </nav>

      <main className="flex flex-col flex-1 bg-void relative min-w-0 overflow-hidden">
        <EngineBanner
          status={liveStatus}
          connectivity={connectivity}
          streamConnected={streamConnected}
          streamError={streamError}
        />

        <div className="flex items-center justify-between px-6 py-2 text-xs border-b border-dim" style={{ backgroundColor: 'var(--bg-surface)' }}>
          <div className="flex items-center gap-6 font-medium">
            <span className={`flex items-center gap-2 ${marketOpen ? 'text-profit' : 'text-muted'}`}>
              <Activity size={14} /> {sessionStatus}
            </span>
            <span className="text-muted">
              State: <span className="font-mono text-main">{liveStatus?.state ?? '—'}</span>
            </span>
            <span className={streamConnected ? 'text-profit font-semibold' : connectivity === 'connected' || connectivity === 'degraded' ? 'text-warn font-semibold' : 'text-muted font-semibold'}>
              {streamConnected ? '● Live SSE' : connectivity === 'loading' ? '○ Connecting' : '○ Polling'}
            </span>
          </div>
          <time
            className="header-ist-clock text-muted font-mono flex-shrink-0 text-right"
            dateTime={new Date().toISOString()}
            title="India Standard Time"
          >
            {istClock}
          </time>
        </div>

        <header className="glass-panel flex justify-between items-center px-6 z-10 gap-4 flex-wrap" style={{ minHeight: '64px' }}>
          <div className="text-xl font-semibold tracking-wide flex-shrink-0">{pageTitle}</div>

          <div className="header-actions gap-3">

            <div className={`flex items-center gap-2 px-4 py-1.5 rounded-full border text-xs font-bold uppercase ${
              engineOnline
                ? 'bg-[rgba(56,189,248,0.1)] border-[rgba(56,189,248,0.2)] text-[#38bdf8]'
                : connectivity === 'loading'
                  ? 'bg-surface-elevated border-dim text-muted'
                  : 'bg-intent-loss-dim border-intent-loss-dim text-loss'
            }`}>
              <Cpu size={14} />
              {connectivity === 'loading' ? 'Connecting…' : engineOnline ? 'Engine Online' : 'Engine Offline'}
            </div>

            <div className="flex items-center gap-2 px-4 py-1.5 rounded-full border border-solid text-xs font-bold bg-surface-elevated">
              <span className={isPaper ? 'text-brand' : 'text-warn'}>
                {isPaper ? 'PAPER MODE' : 'LIVE MODE'}
              </span>
            </div>

            <div className={`flex items-center gap-3 px-5 py-2 rounded-full border ${
              isPaper
                ? 'bg-[rgba(99,102,241,0.1)] border-[rgba(99,102,241,0.3)]'
                : 'bg-intent-profit-dim border-[rgba(16,185,129,0.3)]'
            }`}>
              <span className="text-xs font-bold text-muted">{isPaper ? 'NET P&L' : 'NET MTM'}</span>
              <span className={`font-mono text-lg font-bold tracking-wide ${dailyPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {formatINR(dailyPnl, true)}
              </span>
            </div>

            <button
              onClick={() => setShowKillModal(true)}
              className={`flex items-center gap-2 px-5 py-2 rounded-md font-bold text-sm transition-all border ${
                isKilled
                  ? 'bg-intent-loss text-white border-intent-loss'
                  : 'bg-transparent text-loss border-loss hover:bg-intent-loss hover:text-white'
              }`}
            >
              <Power size={16} />
              {isKilled ? 'KILLED' : 'KILL SWITCH'}
            </button>

            <span className="user-avatar" aria-hidden="true">AE</span>
          </div>
        </header>

        <div className="flex-1 px-6 py-4 overflow-y-auto min-h-0">
          <Outlet context={{ status: liveStatus, stream, connectivity, engineOnline }} />
        </div>
      </main>

      {toast && (
        <div className={`toast toast-${toast.tone}`} role="status">
          {toast.message}
        </div>
      )}

      {showKillModal && (
        <div className="modal-overlay">
          <div className="surface-card p-8 border-loss border-2 modal-card">
            <h2 className="flex items-center gap-3 text-loss text-xl font-bold mb-4">
              <ShieldAlert size={24} /> CONFIRM KILL SWITCH
            </h2>
            <p className="text-main mb-8 leading-relaxed">
              This action will bypass all algorithms, fire market orders to close all open positions, and disable API connections. <strong className="text-white">Are you sure?</strong>
            </p>
            <div className="flex justify-end gap-4">
              <button className="btn btn-secondary" onClick={() => setShowKillModal(false)}>Abort</button>
              <button className="btn btn-danger font-bold tracking-wide" onClick={handleKill}>SQUARE OFF EVERYTHING</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}