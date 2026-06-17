import { Activity, LineChart } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { ExternalJournalStatus, OptionsLegSnapshot, OptionsLegsPayload } from '../api/types';
import { formatINR, formatPrice } from '../utils/format';

const LEG_ORDER = [
  'SENSEX_CE',
  'SENSEX_PE',
  'NIFTY_CE',
  'NIFTY_PE',
  'BANKNIFTY_CE',
  'BANKNIFTY_PE',
] as const;

const INDEX_ROWS = [
  {
    index: 'SENSEX',
    label: 'SENSEX',
    headerClass: 'index-header--sensex',
    ceId: 'SENSEX_CE',
    peId: 'SENSEX_PE',
  },
  {
    index: 'NIFTY',
    label: 'NIFTY 50',
    headerClass: 'index-header--nifty',
    ceId: 'NIFTY_CE',
    peId: 'NIFTY_PE',
  },
  {
    index: 'BANKNIFTY',
    label: 'BANK NIFTY',
    headerClass: 'index-header--banknifty',
    ceId: 'BANKNIFTY_CE',
    peId: 'BANKNIFTY_PE',
  },
] as const;

const JOURNAL_LABELS: Record<ExternalJournalStatus, string> = {
  watching: 'Watching',
  entered: 'In trade',
  target_met: 'Target met',
  stop_hit: 'Stop hit',
  incomplete: 'Incomplete',
  skipped: 'Skipped',
  expired: 'Expired',
};

const EMPTY_LEG = (legId: string, index: string, optionType: 'CE' | 'PE'): OptionsLegSnapshot => ({
  leg_id: legId,
  index,
  leg: optionType === 'CE' ? 'call' : 'put',
  option_type: optionType,
  display_name: `${INDEX_ROWS.find((r) => r.index === index)?.label ?? index} ${optionType}`,
  journal_status: 'watching',
});

function JournalBadge({ status }: { status?: ExternalJournalStatus }) {
  const s = status ?? 'watching';
  return (
    <span className={`journal-badge journal-badge--${s}`}>
      {JOURNAL_LABELS[s]}
    </span>
  );
}

function LiveDot({ live }: { live?: boolean }) {
  return (
    <span className={`options-live-dot ${live ? 'options-live-dot--on' : ''}`} title={live ? 'WebSocket live' : 'Awaiting feed'} />
  );
}

function TargetBand({ leg }: { leg: OptionsLegSnapshot }) {
  const { entry, target, stop_loss: stop, last_ltp: ltp } = leg;
  if (entry == null || target == null || ltp == null) return null;

  const lo = Math.min(entry, target, stop ?? entry);
  const hi = Math.max(entry, target, stop ?? entry);
  const span = hi - lo || 1;
  const pct = Math.max(0, Math.min(100, ((ltp - lo) / span) * 100));
  const entryPct = ((entry - lo) / span) * 100;
  const targetPct = ((target - lo) / span) * 100;
  const inTrade = leg.journal_status === 'entered';

  return (
    <div className="options-target-band" aria-hidden>
      <div className="options-target-band__track">
        <div
          className="options-target-band__zone"
          style={{ left: `${Math.min(entryPct, targetPct)}%`, width: `${Math.abs(targetPct - entryPct)}%` }}
        />
        <span className="options-target-band__mark options-target-band__mark--entry" style={{ left: `${entryPct}%` }} />
        <span className="options-target-band__mark options-target-band__mark--target" style={{ left: `${targetPct}%` }} />
        <span
          className={`options-target-band__ltp ${inTrade ? 'options-target-band__ltp--active' : ''}`}
          style={{ left: `${pct}%` }}
        />
      </div>
      <div className="options-target-band__labels text-2xs font-mono text-muted">
        <span>C {formatPrice(entry)}</span>
        <span>T {formatPrice(target)}</span>
        {stop != null && <span>L {formatPrice(stop)}</span>}
      </div>
    </div>
  );
}

function OptionsSparkline({ values, variant, uid }: { values?: number[]; variant: 'ce' | 'pe'; uid: string }) {
  const w = 200;
  const h = 52;

  if (!values || values.length < 2) {
    return (
      <div className={`options-leg-chart options-leg-chart--${variant} options-leg-chart--empty`}>
        <span className="text-2xs text-muted">Chart warms up with ticks…</span>
      </div>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 8) - 4;
      return `${x},${y}`;
    })
    .join(' ');
  const last = values[values.length - 1];
  const first = values[0];
  const up = last >= first;

  return (
    <div className={`options-leg-chart options-leg-chart--${variant}`}>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id={`grad-${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path
          d={`M0,${h} L${points.replace(/ /g, ' L')} L${w},${h} Z`}
          fill={`url(#grad-${uid})`}
          stroke="none"
        />
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      </svg>
      <span className={`options-leg-chart__delta font-mono text-2xs ${up ? 'text-profit' : 'text-loss'}`}>
        {up ? '+' : ''}{(last - first).toFixed(1)}
      </span>
    </div>
  );
}

function LegChartCard({ leg, configured }: { leg: OptionsLegSnapshot; configured: boolean }) {
  const variant = leg.option_type === 'CE' ? 'ce' : 'pe';
  const isLive = (leg.data_source ?? '').toUpperCase() === 'WS';
  const hasLtp = leg.last_ltp != null && leg.last_ltp > 0;

  return (
    <div className={`options-leg-card options-leg-card--${variant} ${isLive ? 'options-leg-card--live' : ''}`}>
      <div className="options-leg-card__topline">
        <span className="options-leg-card__type">{leg.option_type}</span>
        <div className="options-leg-card__status-row">
          <LiveDot live={isLive} />
          <JournalBadge status={leg.journal_status} />
        </div>
      </div>

      {configured ? (
        <>
          <div className="options-leg-card__ltp-row">
            <span className="options-leg-card__ltp font-mono">{formatPrice(leg.last_ltp)}</span>
            {leg.data_source && leg.data_source !== 'none' && (
              <span className={`badge text-2xs ${isLive ? 'badge-profit' : 'badge-brand'}`}>
                {isLive ? 'LIVE' : leg.data_source}
              </span>
            )}
          </div>

          <div className="options-leg-card__contract text-2xs font-mono text-muted truncate" title={leg.tradingsymbol ?? ''}>
            {leg.strike != null ? `K${leg.strike}` : '—'}
            {leg.tradingsymbol ? ` · ${leg.tradingsymbol}` : ''}
          </div>

          <OptionsSparkline values={leg.sparkline} variant={variant} uid={leg.leg_id} />
          <TargetBand leg={leg} />

          {(leg.session_high != null || leg.session_low != null) && (
            <div className="options-leg-card__session text-2xs text-muted font-mono">
              Sess H {formatPrice(leg.session_high)} · L {formatPrice(leg.session_low)}
            </div>
          )}

          {leg.mtm_net_1lot != null && leg.journal_status === 'entered' && (
            <div className={`options-leg-card__mtm text-xxs font-mono font-semibold ${leg.mtm_net_1lot >= 0 ? 'text-profit' : 'text-loss'}`}>
              MTM 1 lot {formatINR(leg.mtm_net_1lot, true)}
            </div>
          )}
        </>
      ) : (
        <div className="options-leg-card__empty">
          <span className="text-sm text-muted">No strike set</span>
          <Link to="/options-sheet" className="text-xs text-brand hover:underline">
            Add on Options Sheet →
          </Link>
        </div>
      )}

      {!hasLtp && configured && (
        <p className="options-leg-card__note text-2xs text-muted m-0">
          {leg.outcome_note || 'Waiting for Kite premium…'}
        </p>
      )}
    </div>
  );
}

function isLegConfigured(leg: OptionsLegSnapshot): boolean {
  return leg.strike != null || leg.entry != null;
}

function mergeLegsPayload(
  stream?: OptionsLegsPayload | null,
  polled?: OptionsLegsPayload | null,
): OptionsLegsPayload | null {
  if (!stream && !polled) return null;
  const base = stream ?? polled!;
  const legs: Record<string, OptionsLegSnapshot> = { ...base.legs };

  for (const id of LEG_ORDER) {
    const streamLeg = stream?.legs?.[id];
    const polledLeg = polled?.legs?.[id];
    if (streamLeg && polledLeg) {
      const streamAge = streamLeg.data_age_seconds ?? 999;
      const polledAge = polledLeg.data_age_seconds ?? 999;
      legs[id] = streamAge <= polledAge ? streamLeg : polledLeg;
    } else if (streamLeg || polledLeg) {
      legs[id] = (streamLeg ?? polledLeg)!;
    }
  }

  const configured = LEG_ORDER.filter((id) => {
    const leg = legs[id];
    return leg && isLegConfigured(leg);
  }).length;

  return {
    ...base,
    available: configured > 0 || base.available,
    legs,
    date: stream?.date ?? polled?.date ?? base.date,
    summary: stream?.summary ?? polled?.summary ?? base.summary,
    subscribed_tokens: Math.max(stream?.subscribed_tokens ?? 0, polled?.subscribed_tokens ?? 0),
  };
}

interface Props {
  legs?: OptionsLegsPayload | null;
}

export default function OptionsLegsPanel({ legs: streamLegs }: Props) {
  const [polled, setPolled] = useState<OptionsLegsPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    const load = () => {
      api.getOptionsLegsLive()
        .then((r) => {
          setPolled(r);
          setLoadError(null);
        })
        .catch((err: Error) => {
          setLoadError(err.message || 'Failed to load options legs');
        });
    };
    load();
    const pollMs = streamLegs?.available ? 60000 : 15000;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [streamLegs?.available]);

  const payload = useMemo(() => mergeLegsPayload(streamLegs, polled), [streamLegs, polled]);

  const legMap = useMemo(() => {
    const map: Record<string, OptionsLegSnapshot> = {};
    for (const id of LEG_ORDER) {
      const index = id.split('_')[0];
      const opt = id.endsWith('_CE') ? 'CE' : 'PE';
      map[id] = payload?.legs?.[id] ?? EMPTY_LEG(id, index, opt as 'CE' | 'PE');
    }
    return map;
  }, [payload?.legs]);

  const summary = payload?.summary;
  const liveCount = LEG_ORDER.filter((id) => (legMap[id].data_source ?? '').toUpperCase() === 'WS').length;
  const configuredCount = LEG_ORDER.filter((id) => isLegConfigured(legMap[id])).length;

  const feedLabel = liveCount >= configuredCount && configuredCount > 0
    ? 'LIVE'
    : configuredCount > 0
      ? 'PARTIAL'
      : 'SETUP';
  const feedClass = feedLabel === 'LIVE'
    ? 'bg-intent-profit-dim text-profit border border-[rgba(16,185,129,0.3)]'
    : feedLabel === 'PARTIAL'
      ? 'bg-intent-warn-dim text-warn border border-[rgba(245,158,11,0.3)]'
      : 'bg-surface-elevated text-muted border border-dim';

  return (
    <div className="bento-tile bento-tile--auto options-legs-panel options-chart-desk" style={{ gridColumn: 'span 12' }}>
      <div className="tile-section-head">
        <h3 className="tile-section-title m-0">
          <LineChart size={16} /> Sheet Premium Charts
        </h3>
        <div className="flex items-center gap-2">
          {payload?.date && (
            <span className="text-xs text-muted font-mono">{payload.date}</span>
          )}
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${feedClass}`}>
            <Activity size={12} className="inline mr-1" style={{ verticalAlign: '-2px' }} />
            {feedLabel}
          </span>
        </div>
      </div>

      <p className="text-sm text-muted m-0">
        Live CE &amp; PE premiums for your manual sheet legs — NIFTY 50, BANK NIFTY, and SENSEX. Save strikes below to populate charts.
      </p>

      {loadError && !payload && (
        <p className="text-sm text-muted m-0">Feed unavailable — {loadError}</p>
      )}

      {summary && (summary.legs ?? 0) > 0 && (
        <div className="pnl-summary-grid options-legs-summary">
          <div>
            <div className="pnl-summary-label">Session MTM (net)</div>
            <div className={`font-mono text-lg font-bold ${(summary.mtm_net ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
              {formatINR(summary.mtm_net ?? 0, true)}
            </div>
          </div>
          <div>
            <div className="pnl-summary-label">In trade</div>
            <div className="font-mono font-semibold">
              {summary.in_trade ?? 0} / {summary.legs}
            </div>
          </div>
          <div>
            <div className="pnl-summary-label">Live feeds</div>
            <div className="font-mono font-semibold">
              {liveCount} / {configuredCount || 6}
            </div>
          </div>
        </div>
      )}

      <div className="options-chart-rows">
        {INDEX_ROWS.map((row) => (
          <div key={row.index} className="options-index-row">
            <div className={`options-index-row__label ${row.headerClass}`}>
              {row.label}
            </div>
            <div className="options-index-row__pair">
              <LegChartCard leg={legMap[row.ceId]} configured={isLegConfigured(legMap[row.ceId])} />
              <LegChartCard leg={legMap[row.peId]} configured={isLegConfigured(legMap[row.peId])} />
            </div>
          </div>
        ))}
      </div>

      {configuredCount === 0 && (
        <p className="text-xs text-muted m-0">
          <Link to="/options-sheet" className="text-brand hover:underline">Open Options Sheet</Link>
          {' '}to enter strikes and C/T/L for all six legs.
        </p>
      )}
    </div>
  );
}