import { Outlet, NavLink, useLocation } from 'react-router-dom';
import { LayoutDashboard, Wand2, ShieldAlert, History, Settings, Infinity, Power, Activity, Cpu, FileSpreadsheet, BookOpen } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import EngineBanner from './EngineBanner';
import { useStatusStream } from '../hooks/useStatusStream';
import { formatINR } from '../utils/format';
import { computeDailyPnlBreakdown } from '../utils/foCosts';
import type { KiteStatus, SystemStatus } from '../api/types';

export default function Layout() {
  const location = useLocation();
  const [isKilled, setIsKilled] = useState(false);
  const [showKillModal, setShowKillModal] = useState(false);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [kiteStatus, setKiteStatus] = useState<KiteStatus | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: 'ok' | 'warn' | 'error' } | null>(null);
  const { data: stream, connected: streamConnected } = useStatusStream(!isKilled);

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

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [s, k] = await Promise.all([api.getStatus(), api.getKiteStatus()]);
        if (active) {
          setStatus(s);
          setKiteStatus(k);
        }
      } catch {
        if (active) setKiteStatus(null);
      }
    };
    load();
    const id = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const liveStatus = status;
  const isPaper = liveStatus?.mode === 'PAPER';
  const pnlBreakdown = computeDailyPnlBreakdown(
    liveStatus?.daily_pnl ?? 0,
    liveStatus?.options_mtm,
    liveStatus?.per_symbol_status ?? {},
  );
  const dailyPnl = pnlBreakdown.combinedNet;
  const marketOpen = liveStatus?.market?.is_market_open;
  const sessionStatus = liveStatus?.market?.session_status ?? 'UNKNOWN';
  const engineOnline = !liveStatus?.error;

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
      
      {/* Sidebar Navigation */}
      <nav className="app-sidebar flex flex-col border-r border-dim flex-shrink-0">
        <div className="flex items-center gap-3 p-6">
          <div className="flex items-center justify-center text-white rounded-md shadow-[0_0_15px_var(--brand-glow)]" 
               style={{ width: '36px', height: '36px', background: 'linear-gradient(135deg, var(--brand-primary), var(--brand-secondary))' }}>
            <Infinity size={20} />
          </div>
          <div className="text-xl font-bold tracking-wide">
            AG <span className="font-normal text-muted">Quant</span>
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

        {/* Kite API Status Footprint */}
        <div className="flex items-center gap-3 p-6 border-t border-dim mt-auto">
          <div className="rounded-full" style={{ 
            width: '10px', height: '10px', 
            backgroundColor: kiteStatus?.connected ? 'var(--intent-profit)' : 'var(--intent-loss)',
            boxShadow: kiteStatus?.connected ? '0 0 10px var(--intent-profit)' : '0 0 10px var(--intent-loss)'
          }}></div>
          <div className="flex flex-col">
            <span className="text-sm font-semibold">Kite API</span>
            <span className="text-xs text-muted font-mono tracking-wide">
              {kiteStatus?.connected
                ? `Connected • ${kiteStatus.latency_ms ?? '?'}ms`
                : kiteStatus?.error_code ?? 'Offline'}
            </span>
          </div>
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="flex flex-col flex-1 bg-void relative min-w-0 overflow-hidden">
        <EngineBanner status={liveStatus} streamConnected={streamConnected} />

        {/* Market Status Rail */}
        <div className="flex items-center justify-between px-6 py-2 text-xs border-b border-dim" style={{ backgroundColor: 'var(--bg-surface)' }}>
          <div className="flex items-center gap-6 font-medium">
            <span className={`flex items-center gap-2 ${marketOpen ? 'text-profit' : 'text-muted'}`}>
              <Activity size={14} /> {sessionStatus}
            </span>
            <span className="text-muted">
              State: <span className="font-mono text-main">{liveStatus?.state ?? '—'}</span>
            </span>
            <span className={streamConnected ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
              {streamConnected ? '● Live SSE' : '○ Polling'}
            </span>
          </div>
          <span
            className="text-muted font-mono truncate flex-1 min-w-0 text-right"
            title={stream?.last_action ?? liveStatus?.last_action ?? ''}
          >
            {stream?.last_action ?? liveStatus?.last_action ?? 'Waiting for engine...'}
          </span>
        </div>

        {/* Glass Header */}
        <header className="glass-panel flex justify-between items-center px-6 z-10 gap-4 flex-wrap" style={{ minHeight: '64px' }}>
          <div className="text-xl font-semibold tracking-wide flex-shrink-0">{pageTitle}</div>
          
          <div className="header-actions gap-3">
            
            {/* Engine Status Badge */}
            <div className={`flex items-center gap-2 px-4 py-1.5 rounded-full border text-xs font-bold uppercase ${
              engineOnline 
                ? 'bg-[rgba(56,189,248,0.1)] border-[rgba(56,189,248,0.2)] text-[#38bdf8]' 
                : 'bg-intent-loss-dim border-intent-loss-dim text-loss'
            }`}>
              <Cpu size={14} />
              {engineOnline ? 'Engine Online' : 'Engine Offline'}
            </div>

            {/* Trading Mode Badge */}
            <div className="flex items-center gap-2 px-4 py-1.5 rounded-full border border-solid text-xs font-bold bg-surface-elevated">
              <span className={isPaper ? 'text-brand' : 'text-warn'}>
                {isPaper ? 'PAPER MODE' : 'LIVE MODE'}
              </span>
            </div>

            {/* Live PNL Badge */}
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
            
            {/* Kill Switch */}
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
            
            <span className="user-avatar" aria-hidden="true">AQ</span>
          </div>
        </header>

        {/* Scrollable Page Content */}
        <div className="flex-1 px-6 py-4 overflow-y-auto min-h-0">
          <Outlet context={{ status: liveStatus, stream, kiteStatus }} />
        </div>
      </main>

      {/* Emergency Kill Modal */}
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