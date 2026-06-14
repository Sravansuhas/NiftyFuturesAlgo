import { Activity } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type {
  LiveSnapshot,
  PostureSnapshot,
  StatusStreamPayload,
  SystemStatus,
  TradeBudgetSummary,
} from '../api/types';

const INDICES = ['NIFTY', 'BANKNIFTY', 'SENSEX'] as const;

const POSTURE_COLORS: Record<string, string> = {
  aggressive: 'var(--intent-profit)',
  normal: 'var(--brand-primary)',
  defensive: 'var(--intent-warn)',
  contingency: 'var(--intent-loss)',
};

const COLOR_CHIP: Record<string, string> = {
  green: 'badge-profit',
  red: 'badge-loss',
  sideways: 'badge-warn',
};

interface Props {
  status: SystemStatus | null;
  stream: StatusStreamPayload | null;
}

export default function PosturePanel({ status, stream }: Props) {
  const [watchFor, setWatchFor] = useState<string[]>([]);

  useEffect(() => {
    api.getAgentInsights()
      .then((r) => {
        const wf = (r.posture as { watch_for?: string[] } | undefined)?.watch_for;
        if (wf?.length) setWatchFor(wf.slice(0, 3));
      })
      .catch(() => {});
    const id = setInterval(() => {
      api.getAgentInsights()
        .then((r) => {
          const wf = (r.posture as { watch_for?: string[] } | undefined)?.watch_for;
          if (wf?.length) setWatchFor(wf.slice(0, 3));
        })
        .catch(() => {});
    }, 60000);
    return () => clearInterval(id);
  }, []);

  const postureSnap: PostureSnapshot | undefined =
    status?.posture_snapshot ?? (stream as SystemStatus | null)?.posture_snapshot;
  const portfolio = postureSnap?.portfolio;
  const perSymbol = postureSnap?.per_symbol ?? {};
  const tradeBudget: TradeBudgetSummary | undefined = status?.trade_budget;
  const snapshots = stream?.live_snapshots ?? status?.live_snapshots ?? {};

  const posture = portfolio?.posture ?? 'normal';
  const borderColor = POSTURE_COLORS[posture] ?? POSTURE_COLORS.normal;
  const marketColor = portfolio?.market_color ?? 'sideways';
  const chipClass = COLOR_CHIP[marketColor] ?? COLOR_CHIP.sideways;

  return (
    <div
      className="bento-tile flex flex-col gap-4 border-l-4"
      style={{
        gridColumn: 'span 12',
        borderLeftColor: borderColor,
      }}
    >
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-muted uppercase tracking-wider m-0">
          <Activity size={16} /> Session Posture
        </h3>
        <div className="flex gap-2 items-center">
          <span className={`badge ${chipClass}`}>
            {marketColor}
          </span>
          <span className="badge bg-surface-elevated uppercase" style={{ color: borderColor }}>
            {posture}
          </span>
          {portfolio?.risk_multiplier_hint != null && (
            <span className="text-xs font-mono text-muted ml-1">
              risk ×{portfolio.risk_multiplier_hint}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {INDICES.map((sym) => {
          const p = perSymbol[sym];
          const snap = snapshots[sym] as LiveSnapshot | undefined;
          const regime = p?.regime ?? snap?.regime ?? {};
          const budget = tradeBudget?.per_symbol?.[sym];
          const score = budget?.regime_score ?? 0;
          return (
            <div
              key={sym}
              className="p-4 bg-surface-elevated rounded-md border border-dim flex flex-col"
            >
              <div className="flex justify-between items-center mb-2">
                <span className="font-bold text-sm text-main">{sym}</span>
                <span className="text-[0.65rem] font-bold uppercase" style={{ color: POSTURE_COLORS[p?.posture ?? 'normal'] }}>
                  {p?.posture ?? '—'}
                </span>
              </div>
              <div className="text-xs text-muted leading-relaxed mb-3 flex-1">
                {(regime as { trend?: string }).trend ?? '—'}
                {' · '}
                {(regime as { volatility?: string }).volatility ?? '—'}
                {' · HTF '}
                <strong className="text-main">{(regime as { htf_bias?: string }).htf_bias ?? 'neutral'}</strong>
              </div>
              <div className="h-1.5 bg-[var(--border-dim)] rounded-full overflow-hidden mt-auto">
                <div 
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.round(score * 100)}%`,
                    backgroundColor: score >= 0.65 ? 'var(--intent-profit)' : score >= 0.45 ? 'var(--brand-primary)' : 'var(--intent-warn)',
                  }}
                />
              </div>
              <p className="text-[0.65rem] text-muted mt-2 uppercase tracking-wide font-semibold">
                score {(score * 100).toFixed(0)}
                {p?.exit_mode ? ` · exit ${p.exit_mode}` : ''}
                {budget ? ` · ${budget.trades_used}/${budget.effective_cap} trades` : ''}
              </p>
            </div>
          );
        })}
      </div>

      {(portfolio?.reasons?.length ?? 0) > 0 && (
        <div className="text-xs text-muted leading-relaxed mt-2 p-3 bg-[rgba(255,255,255,0.02)] rounded-md border border-dim">
          <strong className="text-main mr-2">Why:</strong>
          {portfolio!.reasons!.slice(0, 4).join(' · ')}
        </div>
      )}

      {(portfolio?.contingencies?.length ?? 0) > 0 && (
        <div className="text-xs p-3 bg-intent-warn-dim rounded-md border border-[rgba(245,158,11,0.2)] leading-relaxed text-main">
          <strong className="text-warn mr-2">Contingencies:</strong>
          {portfolio!.contingencies!.join(' · ')}
        </div>
      )}

      {watchFor.length > 0 && (
        <p className="text-xs text-muted m-0 leading-relaxed">
          <strong className="text-main mr-2">Watch:</strong> {watchFor.join(' · ')}
        </p>
      )}
    </div>
  );
}