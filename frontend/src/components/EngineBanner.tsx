import { AlertTriangle, Terminal, ExternalLink } from 'lucide-react';
import type { SystemStatus } from '../api/types';

interface Props {
  status: SystemStatus | null;
  streamConnected: boolean;
}

export default function EngineBanner({ status, streamConnected }: Props) {
  const engineLoaded = status && !status.error;
  const marketClosed = status?.market?.is_market_open === false;
  const preEventBlock = status?.market?.within_pre_event_block_window === true;
  const isDev = status?.market?.session_status?.includes('DEV') ||
    (status?.state === 'PAPER_MODE' && marketClosed);

  if (engineLoaded && streamConnected) {
    if (preEventBlock) {
      const evt = status?.market?.next_high_impact_event;
      return (
        <div className="flex items-center gap-3 px-8 py-3 bg-intent-loss-dim border-b border-[rgba(239,68,68,0.25)] text-sm">
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
        <div className="flex items-center gap-3 px-8 py-3 bg-intent-warn-dim border-b border-[rgba(245,158,11,0.3)] text-sm">
          <AlertTriangle size={16} className="text-warn flex-shrink-0" />
          <span className="text-main leading-relaxed">
            <strong className="text-warn">Market closed.</strong> Engine is running but prices may be simulated.
            For full testing now, restart with: <code className="font-mono text-brand bg-surface-elevated px-1.5 py-0.5 rounded ml-1">python run.py --dev</code>
          </span>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="flex items-start gap-4 px-8 py-4 bg-[rgba(239,68,68,0.08)] border-b border-[rgba(239,68,68,0.2)] text-sm">
      <Terminal size={20} className="text-loss flex-shrink-0 mt-0.5" />
      <div>
        <div className="font-bold text-loss mb-1.5 text-base">
          Engine not connected — dashboard data will look empty or frozen
        </div>
        <p className="text-muted leading-relaxed mb-3">
          The React UI is not static by design. It reads live data from the Python engine on port <strong className="text-main">8050</strong>.
          You must run both processes:
        </p>
        <ol className="text-main ml-5 list-decimal flex flex-col gap-1.5 font-mono text-xs">
          <li><code className="bg-[rgba(239,68,68,0.15)] text-loss px-1.5 py-0.5 rounded">python run.py --dev</code> <span className="text-muted font-sans ml-1">(engine + API)</span></li>
          <li><code className="bg-surface-elevated px-1.5 py-0.5 rounded">cd frontend && npm run dev</code> <span className="text-muted font-sans ml-1">(this UI)</span></li>
        </ol>
        <p className="text-muted mt-3 flex items-center gap-2">
          Legacy terminal (also live when engine runs):{' '}
          <a href="http://localhost:8050" target="_blank" rel="noreferrer" className="text-brand hover:underline flex items-center gap-1">
            http://localhost:8050 <ExternalLink size={14} />
          </a>
        </p>
      </div>
    </div>
  );
}