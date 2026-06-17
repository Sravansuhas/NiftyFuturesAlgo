import { AlertTriangle, Terminal, ExternalLink, Wifi, Loader2 } from 'lucide-react';
import type { EngineConnectivity } from '../hooks/useEngineConnectivity';
import type { SystemStatus } from '../api/types';

interface Props {
  status: SystemStatus | null;
  connectivity: EngineConnectivity;
  streamConnected?: boolean;
  streamError?: string | null;
}

export default function EngineBanner({
  status,
  connectivity,
  streamConnected = false,
  streamError,
}: Props) {
  const marketClosed = status?.market?.is_market_open === false;
  const preEventBlock = status?.market?.within_pre_event_block_window === true;
  const isDev = status?.market?.session_status?.includes('DEV') ||
    (status?.state === 'PAPER_MODE' && marketClosed);

  if (connectivity === 'loading') {
    return (
      <div className="engine-banner engine-banner--loading">
        <Loader2 size={16} className="text-brand flex-shrink-0 animate-spin" />
        <span className="text-main">
          Connecting to engine on port <strong className="text-brand">8050</strong>…
        </span>
      </div>
    );
  }

  if (connectivity === 'api_down') {
    const port = window.location.port;
    const onViteDev = port === '5173' || port === '4173';
    const onBuiltUi = port === '8050' || port === '';
    const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';
    return (
      <div className="engine-banner engine-banner--error">
        <Terminal size={20} className="text-loss flex-shrink-0 mt-0.5" />
        <div className="engine-banner__body">
          <div className="engine-banner__title text-loss">
            Cannot reach API on port 8050
          </div>
          <p className="text-muted leading-relaxed mb-3">
            {onBuiltUi
              ? 'run.py may still be starting, or the API is overloaded (slow Kite calls block /health). Wait for "API ready" in the terminal, then hard-refresh (Ctrl+Shift+R).'
              : onViteDev
                ? 'This page is on the Vite dev server — it proxies /health and /api to run.py on :8050.'
                : 'Open the built UI on the same port as run.py, or wait until the terminal prints "API ready".'}
            {' '}Then hard-refresh (Ctrl+Shift+R).
          </p>
          <ol className="text-main ml-5 list-decimal flex flex-col gap-1.5 font-mono text-xs">
            <li><code className="bg-[rgba(239,68,68,0.15)] text-loss px-1.5 py-0.5 rounded">python run.py --dev</code> <span className="text-muted font-sans ml-1">(engine + API)</span></li>
            {onViteDev && (
              <li><code className="bg-surface-elevated px-1.5 py-0.5 rounded">cd frontend && npm run dev</code> <span className="text-muted font-sans ml-1">(keep this terminal open)</span></li>
            )}
            <li className="font-sans text-muted list-none -ml-5 mt-1">
              Recommended: <a href="http://127.0.0.1:8050/ui/dashboard" className="text-brand hover:underline">http://127.0.0.1:8050/ui/dashboard</a>
              {apiBase ? <span className="block mt-1">VITE_API_BASE={apiBase}</span> : null}
            </li>
          </ol>
          <p className="text-muted mt-3 flex items-center gap-2">
            Legacy terminal:{' '}
            <a href="http://localhost:8050" target="_blank" rel="noreferrer" className="text-brand hover:underline flex items-center gap-1">
              http://localhost:8050 <ExternalLink size={14} />
            </a>
          </p>
        </div>
      </div>
    );
  }

  if (connectivity === 'engine_offline') {
    return (
      <div className="engine-banner engine-banner--error">
        <Terminal size={20} className="text-loss flex-shrink-0 mt-0.5" />
        <div className="engine-banner__body">
          <div className="engine-banner__title text-loss">
            API reachable — trading engine not loaded
          </div>
          <p className="text-muted leading-relaxed">
            Use <code className="font-mono text-xs bg-surface-elevated px-1.5 py-0.5 rounded">python run.py --dev</code> (not standalone uvicorn).
            {status?.error && (
              <> Reported: <code className="text-loss text-xs">{status.error}</code></>
            )}
          </p>
        </div>
      </div>
    );
  }

  if (preEventBlock) {
    const evt = status?.market?.next_high_impact_event;
    return (
      <div className="engine-banner engine-banner--alert">
        <AlertTriangle size={16} className="text-loss flex-shrink-0" />
        <span className="text-main leading-relaxed">
          <strong className="text-loss">Pre-event block active.</strong> No new entries within 4h of{' '}
          {evt?.name ?? 'high-impact macro event'} (FO_EVENT_CALENDAR).
        </span>
      </div>
    );
  }

  if (marketClosed && !isDev) {
    return (
      <div className="engine-banner engine-banner--warn">
        <AlertTriangle size={16} className="text-warn flex-shrink-0" />
        <span className="text-main leading-relaxed">
          <strong className="text-warn">Market closed.</strong> Engine is running but prices may be simulated.
          For full testing now, restart with: <code className="font-mono text-brand bg-surface-elevated px-1.5 py-0.5 rounded ml-1">python run.py --dev</code>
        </span>
      </div>
    );
  }

  return (
    <div className="engine-banner engine-banner--ok">
      <Wifi size={12} className="text-profit" />
      <span>
        Engine connected
        {streamConnected ? ' + live stream' : ' (REST polling)'}
        {streamError && !streamConnected ? ` — ${streamError}` : ''}
      </span>
    </div>
  );
}