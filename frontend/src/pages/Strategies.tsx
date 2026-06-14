import { Layers, Pause, Play, TrendingUp } from 'lucide-react';
import { useOutletContext } from 'react-router-dom';
import EmptyState from '../components/ui/EmptyState';
import PageShell from '../components/ui/PageShell';
import type { LiveSnapshot, PerSymbolStatus, StatusStreamPayload, SystemStatus } from '../api/types';
import { formatINR, formatPrice } from '../utils/format';

interface OutletContext {
  status: SystemStatus | null;
  stream: StatusStreamPayload | null;
}

const STRATEGY_META: Record<string, { name: string; desc: string }> = {
  NIFTY: {
    name: 'Previous Candle Breakout',
    desc: '5-min previous-range breakout with ATR-based targets and regime filter.',
  },
  BANKNIFTY: {
    name: 'Previous Candle Breakout',
    desc: 'Same logic adapted for BankNifty futures with symbol-aware lot sizing.',
  },
  SENSEX: {
    name: 'Previous Candle Breakout',
    desc: 'BSE Sensex futures breakout — monitored with staleness detection.',
  },
};

function EngineToggle({ active }: { active: boolean }) {
  return (
    <div
      title="Engine-controlled — pause via kill switch or EMERGENCY_HALT"
      className="relative flex-shrink-0 rounded-full opacity-85"
      style={{
        width: 44,
        height: 24,
        backgroundColor: active ? 'var(--intent-profit)' : 'var(--border-solid)',
      }}
    >
      <div
        className="absolute rounded-full bg-white"
        style={{
          width: 18,
          height: 18,
          top: 3,
          left: 3,
          transform: active ? 'translateX(20px)' : 'translateX(0)',
          transition: 'transform 0.2s ease',
        }}
      />
    </div>
  );
}

export default function Strategies() {
  const { status, stream } = useOutletContext<OutletContext>();
  const snapshots = stream?.live_snapshots ?? status?.live_snapshots ?? {};
  const perSymbol = stream?.per_symbol_status ?? status?.per_symbol_status ?? {};
  const engineState = status?.state ?? 'BOOTING';
  const isRunning = engineState !== 'TRADING_DISABLED' && engineState !== 'EMERGENCY_HALT' && !status?.error;

  const strategies = (['NIFTY', 'BANKNIFTY', 'SENSEX'] as const).map((sym, id) => {
    const snap = snapshots[sym] as LiveSnapshot | undefined;
    const pos = perSymbol[sym] as PerSymbolStatus | undefined;
    const meta = STRATEGY_META[sym];
    const active = isRunning;
    const pnl = pos?.daily_pnl ?? 0;

    return {
      id: id + 1,
      sym,
      name: `${sym} — ${meta.name}`,
      desc: meta.desc,
      inst: sym,
      status: active ? 'Running' : 'Paused',
      pnl,
      active,
      snap,
      pos,
    };
  });

  const engineOnline = status && !status.error;

  return (
    <PageShell subtitle={`Live 3-index futures breakout engine — ${status?.mode ?? 'PAPER'} mode · state ${engineState}`}>
      {!engineOnline && (
        <EmptyState
          variant="centered"
          title="Engine offline"
          message="Start python run.py --dev to load live strategy snapshots for NIFTY, BANKNIFTY, and SENSEX."
        />
      )}

      <div className="flex flex-col gap-3">
        {strategies.map((s) => (
          <div
            key={s.sym}
            className="bento-tile"
            style={{ borderColor: s.active ? 'var(--brand-primary)' : 'var(--border-dim)' }}
          >
            <div className="flex justify-between items-start gap-4 mb-1">
              <div className="min-w-0 flex-1">
                <h4 className="text-lg font-semibold m-0 mb-1">{s.name}</h4>
                <p className="text-sm text-muted m-0 leading-relaxed">{s.desc}</p>
              </div>
              <EngineToggle active={s.active} />
            </div>

            <div className="stat-strip">
              <div className="stat-strip-item">
                <span className="stat-strip-label">Status</span>
                <span className={`stat-strip-value flex items-center gap-1.5 ${s.active ? 'text-profit' : 'text-main'}`}>
                  {s.active ? <Play size={14} /> : <Pause size={14} />} {s.status}
                </span>
              </div>
              <div className="stat-strip-item">
                <span className="stat-strip-label">Contract</span>
                <span className="stat-strip-value">{s.snap?.symbol ?? s.inst}</span>
              </div>
              <div className="stat-strip-item">
                <span className="stat-strip-label">LTP</span>
                <span className="stat-strip-value">{formatPrice(s.snap?.ltp)}</span>
              </div>
              <div className="stat-strip-item">
                <span className="stat-strip-label">Signal</span>
                <span className={`stat-strip-value ${s.snap?.proposed === 'LONG' ? 'text-profit' : s.snap?.proposed === 'SHORT' ? 'text-loss' : 'text-muted'}`}>
                  {s.snap?.proposed ?? 'FLAT'}
                </span>
              </div>
              <div className="stat-strip-item">
                <span className="stat-strip-label">Today&apos;s P&L</span>
                <span className={`stat-strip-value ${s.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {formatINR(s.pnl, true)}
                </span>
              </div>
            </div>

            <div className="metric-stack">
              <h5 className="text-sm text-main m-0 flex items-center gap-2">
                <TrendingUp size={16} className="text-brand" /> Live Parameters
              </h5>
              <div className="param-grid">
                <div>
                  <span className="param-cell-label">ATR</span>
                  <div className="param-cell-value">{formatPrice(s.snap?.fast_atr ?? s.snap?.atr)}</div>
                </div>
                <div>
                  <span className="param-cell-label">Target</span>
                  <div className="param-cell-value text-profit">{formatPrice(s.snap?.target)}</div>
                </div>
                <div>
                  <span className="param-cell-label">Stop Loss</span>
                  <div className="param-cell-value text-loss">{formatPrice(s.snap?.stop_loss)}</div>
                </div>
                <div>
                  <span className="param-cell-label">Confidence</span>
                  <div className="param-cell-value">{((s.snap?.confidence ?? 0) * 100).toFixed(0)}%</div>
                </div>
              </div>
            </div>

            <div className="metric-stack mt-1">
              <h5 className="text-sm text-main m-0 flex items-center gap-2">
                <Layers size={16} className="text-brand" /> Position
              </h5>
              <div className="chip-row">
                <span className="chip-tag">Qty: {s.pos?.position ?? 0}</span>
                <span className="chip-tag">Avg: {formatPrice(s.pos?.avg_price)}</span>
                <span className="chip-tag">Regime: {s.snap?.regime?.volatility ?? 'normal'}</span>
                <span className="chip-tag">Source: {s.snap?.data_source ?? '—'}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </PageShell>
  );
}