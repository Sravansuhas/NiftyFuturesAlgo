import { Activity, Bot, ShieldCheck, XCircle } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type {
  GammaCautionLevel,
  OptionsAlgoLeg,
  OptionsAlgoRegimeGates,
  OptionsAlgoStatus,
  OptionsAlgoStructure,
  RecentExecution,
  SystemStatus,
} from '../api/types';
import { eventLabel, eventText, formatINR, formatPrice, formatTime } from '../utils/format';
import OptionsIndexTickerGrid from './OptionsIndexTickerGrid';

const LEG_ROLE_ORDER = ['put_long', 'put_short', 'call_short', 'call_long'] as const;

const LEG_ROLE_LABELS: Record<string, string> = {
  put_long: 'Put wing',
  put_short: 'Put short',
  call_short: 'Call short',
  call_long: 'Call wing',
};

const GAMMA_CAUTION_LABELS: Record<GammaCautionLevel, string> = {
  0: 'Clear',
  1: 'Caution',
  2: 'Blocked',
};

const GAMMA_CAUTION_BADGE: Record<GammaCautionLevel, string> = {
  0: 'badge-profit',
  1: 'badge-warn',
  2: 'badge-loss',
};

/** Calendar-based expiry triggers — avoid implying live gamma math in copy */
const CALENDAR_TRIGGER_TYPES = new Set([
  'calendar_soft',
  'calendar_hard',
  'expiry_morning',
  'expiry_morning_window',
  'expiry_cutoff',
  'post_cutoff',
  'expiry_after_cutoff',
  'expiry_calendar',
  'calendar_gamma',
  'expiry_day_block',
]);

function resolveGammaCautionLevel(regime?: OptionsAlgoRegimeGates | null): GammaCautionLevel | null {
  if (!regime) return null;
  const level = regime.gamma_caution_level;
  if (level === 0 || level === 1 || level === 2) return level;
  const passed = regime.passed ?? regime.allowed ?? false;
  if (!passed) return 2;
  if (regime.expiry_caution) return 1;
  return 0;
}

function resolveTriggerType(regime?: OptionsAlgoRegimeGates | null): string | null {
  if (!regime) return null;
  if (regime.trigger_type) return regime.trigger_type;
  const reasons = regime.reasons ?? [];
  if (reasons.some((r) => /market closed/i.test(r))) return 'market_closed';
  if (reasons.some((r) => /max_vix|above max/i.test(r))) return 'vix_high';
  if (reasons.some((r) => /min_vix|below min/i.test(r))) return 'vix_low';
  if (reasons.some((r) => /entry blocked/i.test(r))) return 'expiry_day_block';
  if (reasons.some((r) => /after \d{2}:00|gamma caution/i.test(r))) return 'expiry_cutoff';
  if (regime.is_expiry_day && regime.expiry_caution) return 'expiry_morning_window';
  if (regime.is_expiry_day && !(regime.passed ?? regime.allowed)) return 'expiry_cutoff';
  return null;
}

function formatCutoffHour(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00 IST`;
}

function formatTriggerLabel(triggerType: string | null, cutoffHour: number): string | null {
  if (!triggerType) return null;
  const cutoff = formatCutoffHour(cutoffHour);
  const normalized = triggerType.toLowerCase().replace(/-/g, '_');
  switch (normalized) {
    case 'calendar_soft':
    case 'expiry_morning':
    case 'expiry_morning_window':
      return `Expiry morning window (until ${cutoff})`;
    case 'calendar_hard':
    case 'expiry_cutoff':
    case 'post_cutoff':
    case 'expiry_after_cutoff':
      return `Post-${String(cutoffHour).padStart(2, '0')}:00 cutoff`;
    case 'gamma_proxy_soft':
      return 'Gamma proxy — elevated (soft caution)';
    case 'gamma_proxy_hard':
    case 'gamma_proxy':
      return 'Gamma proxy — elevated (hard block)';
    case 'calendar_gamma':
    case 'expiry_calendar':
      return 'Expiry calendar caution';
    case 'expiry_day_block':
      return 'Expiry day — entries blocked';
    case 'vix_high':
      return 'India VIX above cap';
    case 'vix_low':
      return 'India VIX below floor';
    case 'market_closed':
      return 'Market closed';
    default:
      return triggerType.replace(/_/g, ' ');
  }
}

function isCalendarOnlyTrigger(triggerType: string | null): boolean {
  if (!triggerType) return false;
  const normalized = triggerType.toLowerCase().replace(/-/g, '_');
  return CALENDAR_TRIGGER_TYPES.has(normalized);
}

function collectExpiryChips(regime?: OptionsAlgoRegimeGates | null): string[] {
  if (!regime) return [];
  const expiry = [
    ...(regime.expiry_triggers ?? []),
    ...(regime.expiry_reasons ?? []),
  ];
  if (expiry.length > 0) return [...new Set(expiry)];
  if (regime.is_expiry_day) {
    return [`${regime.underlying ?? 'Underlying'} expiry day`];
  }
  return [];
}

function regimeGateSummary(
  regime: OptionsAlgoRegimeGates | null | undefined,
  enabled: boolean,
  cautionLevel: GammaCautionLevel | null,
  triggerLabel: string | null,
  triggerType: string | null,
  cutoffHour: number,
): string {
  if (!enabled) {
    return 'Enable algo in Settings → Trading Controls.';
  }
  if (!regime) {
    return 'Waiting for regime gate status from the runner.';
  }
  if (cautionLevel === 2) {
    return triggerLabel
      ? `${triggerLabel} — new iron condor entries blocked. Exits and MTM management still run.`
      : 'Gates blocking new entries. Exits and MTM management still run.';
  }
  if (cautionLevel === 1) {
    if (isCalendarOnlyTrigger(triggerType)) {
      return triggerLabel
        ? `${triggerLabel} — calendar discipline only (no live gamma model). New entries allowed until cutoff.`
        : `Expiry calendar caution — new entries allowed until ${formatCutoffHour(cutoffHour)}.`;
    }
    return triggerLabel
      ? `${triggerLabel} — tighter entry discipline; runner still evaluates when flat.`
      : 'Caution active — runner still evaluates new structures when flat.';
  }
  return 'Gates clear — runner will propose new iron condors when flat and capital allows.';
}

function emptyStructuresHint(
  enabled: boolean,
  cautionLevel: GammaCautionLevel | null,
  triggerLabel: string | null,
  triggerType: string | null,
  cutoffHour: number,
): string {
  if (!enabled) {
    return ' Enable the algo in Settings to start automated trading.';
  }
  if (cautionLevel === 2) {
    return triggerLabel
      ? ` ${triggerLabel} — waiting for gates to clear before new entries.`
      : ' Regime gates are blocking new entries.';
  }
  if (cautionLevel === 1) {
    if (isCalendarOnlyTrigger(triggerType)) {
      return triggerLabel
        ? ` ${triggerLabel} — calendar window only; runner will propose on the next cycle when flat.`
        : ` Expiry morning window until ${formatCutoffHour(cutoffHour)} — runner will propose when flat.`;
    }
    return triggerLabel
      ? ` ${triggerLabel} — runner will propose on the next cycle when flat.`
      : ' Caution active — runner will propose on the next cycle when flat.';
  }
  if (cautionLevel === 0) {
    return ' The runner will propose on the next cycle when gates hold and capital allows.';
  }
  return ' Waiting for regime gate status.';
}

function inferLegRole(leg: OptionsAlgoLeg): string {
  if (leg.role) return leg.role;
  const opt = (leg.option_type ?? '').toUpperCase();
  const side = (leg.transaction_type ?? '').toUpperCase();
  if (opt === 'PE' && side === 'BUY') return 'put_long';
  if (opt === 'PE' && side === 'SELL') return 'put_short';
  if (opt === 'CE' && side === 'SELL') return 'call_short';
  if (opt === 'CE' && side === 'BUY') return 'call_long';
  return 'leg';
}

function isOptionsExecutionEvent(e: RecentExecution): boolean {
  const type = String(e.type ?? '').toLowerCase();
  if (type.startsWith('options.')) return true;
  const sym = String(e.symbol ?? '').toUpperCase();
  return sym.includes('CE') || sym.includes('PE') || type.includes('option');
}

function isOptionsLedgerEvent(t: Record<string, unknown>): boolean {
  const eventType = String(t.event_type ?? '').toLowerCase();
  if (eventType.startsWith('options.')) return true;
  const p = (t.payload as Record<string, unknown>) ?? {};
  const sym = String(p.symbol ?? p.index ?? p.underlying ?? '').toUpperCase();
  return sym.includes('CE') || sym.includes('PE') || eventType.includes('option');
}

function isTodayIstEvent(e: RecentExecution | Record<string, unknown>): boolean {
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
  const raw = e as Record<string, unknown>;
  const dateIst = raw.date_ist;
  if (typeof dateIst === 'string' && dateIst) return dateIst === today;
  const ts = raw.ts;
  const tsNum = typeof ts === 'number' ? ts : typeof ts === 'string' ? Number(ts) : NaN;
  if (Number.isFinite(tsNum) && tsNum > 0) {
    return new Date(tsNum * 1000).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }) === today;
  }
  return true;
}

function sortLegs(legs: OptionsAlgoLeg[]): OptionsAlgoLeg[] {
  return [...legs].sort((a, b) => {
    const ai = LEG_ROLE_ORDER.indexOf(inferLegRole(a) as (typeof LEG_ROLE_ORDER)[number]);
    const bi = LEG_ROLE_ORDER.indexOf(inferLegRole(b) as (typeof LEG_ROLE_ORDER)[number]);
    const aIdx = ai >= 0 ? ai : 99;
    const bIdx = bi >= 0 ? bi : 99;
    if (aIdx !== bIdx) return aIdx - bIdx;
    return (a.strike ?? 0) - (b.strike ?? 0);
  });
}

function StructureCard({
  structure,
  closing,
  onClose,
}: {
  structure: OptionsAlgoStructure;
  closing: boolean;
  onClose: (id: string) => void;
}) {
  const legs = sortLegs(structure.legs ?? []);
  const mtm = structure.mtm_estimate ?? structure.mtm;

  return (
    <div className="options-algo-structure">
      <div className="options-algo-structure__head">
        <div>
          <span className="options-algo-structure__id font-mono text-xs">{structure.structure_id}</span>
          <div className="options-algo-structure__meta text-2xs text-muted">
            {structure.underlying} · {structure.structure_type.replace(/_/g, ' ')}
            {structure.expiry && <> · exp {structure.expiry}</>}
            {structure.opened_at && <> · opened {formatTime(structure.opened_at)}</>}
          </div>
        </div>
        <div className="options-algo-structure__actions">
          {mtm != null && (
            <span className={`font-mono font-bold text-lg ${mtm >= 0 ? 'text-profit' : 'text-loss'}`}>
              {formatINR(mtm, true)}
            </span>
          )}
          <button
            type="button"
            className="btn btn-danger btn--compact"
            disabled={closing || structure.status !== 'OPEN'}
            onClick={() => onClose(structure.structure_id)}
          >
            {closing ? 'Closing…' : 'Close structure'}
          </button>
        </div>
      </div>

      <div className="options-algo-structure__economics">
        <div>
          <span className="pnl-summary-label">Entry credit</span>
          <span className="font-mono text-sm">{formatINR(structure.entry_credit ?? 0)}</span>
        </div>
        <div>
          <span className="pnl-summary-label">Max loss</span>
          <span className="font-mono text-sm text-loss">{formatINR(structure.max_loss ?? 0)}</span>
        </div>
      </div>

      {legs.length > 0 && (
        <table className="data-table data-table--dense options-algo-legs-table">
          <thead>
            <tr>
              {['Leg', 'Side', 'Strike', 'LTP', 'Qty'].map((h) => (
                <th key={h} className="text-left">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {legs.map((leg, i) => {
              const role = inferLegRole(leg);
              const isSell = (leg.transaction_type ?? '').toUpperCase() === 'SELL';
              return (
                <tr key={`${role}-${leg.strike}-${i}`}>
                  <td className="text-2xs whitespace-nowrap">{LEG_ROLE_LABELS[role] ?? role}</td>
                  <td>
                    <span className={`badge text-2xs ${isSell ? 'badge-warn' : 'badge-brand'}`}>
                      {leg.transaction_type ?? '—'}
                    </span>
                  </td>
                  <td className="font-mono">{leg.strike ?? '—'}</td>
                  <td className="font-mono font-semibold">{formatPrice(leg.last_ltp ?? leg.premium)}</td>
                  <td className="font-mono text-muted">{leg.quantity ?? '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface Props {
  status?: SystemStatus | null;
  recentExecutions?: RecentExecution[];
  recentTrades?: Array<Record<string, unknown>>;
  marketOpen?: boolean;
  sessionStatus?: string;
}

export default function OptionsAlgoPanel({
  status: systemStatus,
  recentExecutions = [],
  recentTrades = [],
  marketOpen,
  sessionStatus,
}: Props) {
  const [status, setStatus] = useState<OptionsAlgoStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [closingId, setClosingId] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api.getOptionsAlgoStatus({ fast: true })
      .then((r) => {
        setStatus(r);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        const raw = err instanceof Error ? err.message : 'Failed to load options algo status';
        const msg = raw.includes('timed out')
          ? 'Status API slow — desk still polling (not necessarily a dead Kite feed)'
          : raw;
        setLoadError(msg);
      });
  }, []);

  useEffect(() => {
    load();
    const pollMs = marketOpen === false ? 45000 : 20000;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [load, marketOpen]);

  const handleClose = async (structureId: string) => {
    if (!window.confirm(`Close structure ${structureId}? Exit orders go through RiskGatekeeper.`)) {
      return;
    }
    setClosingId(structureId);
    setActionMsg(null);
    try {
      const res = await api.closeOptionsAlgoStructure(structureId);
      if (res.success ?? res.ok) {
        setActionMsg(res.message ?? `Closed ${structureId}`);
        load();
      } else {
        setActionMsg(res.message ?? res.error ?? 'Close request failed');
      }
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : 'Close request failed');
    } finally {
      setClosingId(null);
    }
  };

  const enabledFlags = typeof status?.enabled === 'object' ? status.enabled : null;
  const enabled = enabledFlags?.options_trading
    ?? (typeof status?.enabled === 'boolean' ? status.enabled : false);
  const regime = status?.regime_gates;
  const openStructures = useMemo(
    () => (status?.open_structures ?? []).filter((s) => s.status === 'OPEN'),
    [status?.open_structures],
  );

  const mtmTotal = status?.mtm_estimate?.total
    ?? openStructures.reduce((sum, s) => sum + (s.mtm_estimate ?? s.mtm ?? 0), 0);
  const mtmAvailable = status?.mtm_estimate?.available
    ?? openStructures.some((s) => s.mtm_estimate != null || s.mtm != null);

  const lastCycle = status?.last_cycle ?? status?.last_cycle_result;
  const maxPerDay = status?.max_structures_per_day ?? status?.config?.max_structures_per_day;
  const underlying = status?.underlying ?? status?.config?.underlying ?? 'NIFTY';
  const isPaper = systemStatus?.mode === 'PAPER';
  const equity = systemStatus?.current_equity ?? systemStatus?.capital;
  const dailyPnl = systemStatus?.daily_pnl ?? 0;

  const cautionLevel = resolveGammaCautionLevel(regime);
  const triggerType = resolveTriggerType(regime);
  const expiryCutoff = regime?.expiry_entry_cutoff_hour ?? 12;
  const triggerLabel = formatTriggerLabel(triggerType, expiryCutoff);
  const expiryChips = collectExpiryChips(regime);
  const gateChips = (regime?.reasons?.length ? regime.reasons : expiryChips);
  const gatesAllowEntries = cautionLevel === 0 || cautionLevel === 1;
  const regimeClass = cautionLevel != null
    ? GAMMA_CAUTION_BADGE[cautionLevel]
    : 'badge-muted';
  const regimeLabel = cautionLevel != null
    ? GAMMA_CAUTION_LABELS[cautionLevel]
    : '—';
  const gateSummary = regimeGateSummary(
    regime,
    enabled,
    cautionLevel,
    triggerLabel,
    triggerType,
    expiryCutoff,
  );

  const algoLogs = recentExecutions
    .filter(isOptionsExecutionEvent)
    .filter(isTodayIstEvent)
    .slice(0, 6)
    .map((e) => ({
      time: formatTime(e.ts),
      type: eventLabel(e.type),
      text: eventText(e),
    }));

  const ledgerEvents = recentTrades
    .filter(isOptionsLedgerEvent)
    .filter(isTodayIstEvent)
    .slice(0, 6);

  return (
    <div className="bento-tile bento-tile--auto options-algo-panel options-desk">
      <div className="options-desk-header tile-section-head">
        <h3 className="tile-section-title m-0">
          <Bot size={18} /> Live Options Desk
        </h3>
        <div className="options-desk-status-pills">
          {status?.session_date && (
            <span className="options-desk-pill options-desk-pill--date font-mono">
              {status.session_date}
            </span>
          )}
          <span className="options-desk-pill options-desk-pill--mode">
            {isPaper ? 'Paper' : 'Live'}
          </span>
          <span className={`options-desk-pill ${enabled ? 'options-desk-pill--algo-on' : 'options-desk-pill--algo-off'}`}>
            {enabled ? 'ALGO ON' : 'ALGO OFF'}
          </span>
        </div>
      </div>

      {loadError && (
        <p className="message-banner message-banner--warn m-0">Options desk status delayed — {loadError}</p>
      )}

      {actionMsg && (
        <p className="message-banner message-banner--info m-0">{actionMsg}</p>
      )}

      {marketOpen === false && (
        <div className="session-status-banner session-status-banner--closed" role="status">
          <strong>Market closed.</strong> Algo runner is idle; live tape mood is unavailable.
          REST tickers may still show last-session quotes until the next open.
          {sessionStatus && (
            <span className="session-status-banner__tag font-mono">{sessionStatus}</span>
          )}
        </div>
      )}

      <div className="options-desk-kpi-grid">
        <div className="options-desk-kpi options-desk-kpi--hero">
          <span className="options-desk-kpi__label">Algo MTM (open)</span>
          <span className={`options-desk-kpi__value options-desk-kpi__value--hero ${mtmAvailable ? (mtmTotal >= 0 ? 'text-profit' : 'text-loss') : 'text-muted'}`}>
            {mtmAvailable ? formatINR(mtmTotal, true) : '—'}
          </span>
        </div>
        <div className="options-desk-kpi">
          <span className="options-desk-kpi__label">Open structures</span>
          <span className="options-desk-kpi__value font-mono">
            {openStructures.length}
            {maxPerDay != null && <span className="options-desk-kpi__suffix"> / {maxPerDay}</span>}
          </span>
        </div>
        <div className="options-desk-kpi">
          <span className="options-desk-kpi__label">Today</span>
          <span className="options-desk-kpi__value font-mono">{status?.structures_today ?? 0}</span>
        </div>
        <div className="options-desk-kpi">
          <span className="options-desk-kpi__label">Underlying</span>
          <span className="options-desk-kpi__value font-mono">{underlying}</span>
        </div>
        {equity != null && (
          <div className="options-desk-kpi">
            <span className="options-desk-kpi__label">Equity</span>
            <span className="options-desk-kpi__value font-mono">{formatINR(equity)}</span>
          </div>
        )}
        {systemStatus && (
          <div className="options-desk-kpi">
            <span className="options-desk-kpi__label">Session P&L</span>
            <span className={`options-desk-kpi__value font-mono ${dailyPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
              {formatINR(dailyPnl, true)}
            </span>
          </div>
        )}
      </div>

      <OptionsIndexTickerGrid marketOpen={marketOpen} />

      {!enabled && (
        <div className="options-algo-banner options-algo-banner--warn">
          <XCircle size={14} className="options-algo-banner__icon" />
          Algo disabled — turn on <strong>Options algo (Iron Condor)</strong> in{' '}
          <Link to="/settings" className="text-brand hover:underline">Settings → Trading Controls</Link>.
        </div>
      )}

      <div className="options-desk-main">
        <div className="options-desk-main__col options-desk-main__col--structures">
          <div className="options-desk-structures-head">
            <h4 className="tile-section-title m-0 text-sm">Open structures</h4>
            <span className="text-xs text-muted">{openStructures.length} active</span>
          </div>

          {openStructures.length === 0 ? (
            <div className="options-desk-empty">
              <p className="text-sm text-muted m-0">
                No open iron condor structures.
                {emptyStructuresHint(enabled, cautionLevel, triggerLabel, triggerType, expiryCutoff)}
              </p>
            </div>
          ) : (
            <div className="options-algo-structures">
              {openStructures.map((s) => (
                <StructureCard
                  key={s.structure_id}
                  structure={s}
                  closing={closingId === s.structure_id}
                  onClose={handleClose}
                />
              ))}
            </div>
          )}
        </div>

        <div className="options-desk-main__col options-desk-main__col--sidebar">
          <div className="options-algo-gates">
            <div className="options-algo-gates__head">
              <ShieldCheck size={14} />
              <span className="text-sm font-semibold">Regime gates</span>
              <span className={`badge ${regimeClass}`}>
                {regimeLabel}
              </span>
              {triggerLabel && (
                <span className="text-xs text-muted">{triggerLabel}</span>
              )}
              {regime?.is_expiry_day && (
                <span className="badge badge-warn text-2xs">Expiry day</span>
              )}
              {regime?.vix_level != null && (
                <span className="options-algo-gates__vix text-xs text-muted font-mono">VIX {regime.vix_level.toFixed(1)}</span>
              )}
            </div>
            {gateChips.length > 0 ? (
              <div className="guard-chip-row">
                {gateChips.map((reason) => (
                  <span
                    key={reason}
                    className={`guard-chip ${gatesAllowEntries ? '' : 'guard-chip--blocked'}`}
                  >
                    {reason}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted m-0">{gateSummary}</p>
            )}
          </div>

          <div className="options-desk-cycle-card">
            <div className="options-desk-cycle-card__head">
              <Activity size={14} />
              <span className="text-sm font-semibold">Cycle status</span>
            </div>
            {(lastCycle?.skipped || lastCycle?.action) ? (
              <p className="options-desk-cycle-card__body text-xs text-muted m-0">
                {lastCycle.skipped ? (
                  <>Last cycle skipped: <span className="font-mono text-main">{lastCycle.reason}</span></>
                ) : lastCycle.success === false ? (
                  <>
                    Last cycle <span className="text-danger font-semibold">failed</span>:{' '}
                    <span className="font-mono text-main">{lastCycle.action}</span>
                    {lastCycle.message && (
                      <> — <span className="font-mono text-main">{lastCycle.message}</span></>
                    )}
                    {status?.last_cycle_at && (
                      <> @ <span className="font-mono">{formatTime(status.last_cycle_at)}</span></>
                    )}
                  </>
                ) : (
                  <>
                    Last cycle: <span className="font-mono text-main">{lastCycle.action}</span>
                    {status?.last_cycle_at && (
                      <> @ <span className="font-mono">{formatTime(status.last_cycle_at)}</span></>
                    )}
                  </>
                )}
              </p>
            ) : (
              <p className="options-desk-cycle-card__body text-xs text-muted m-0">
                No cycle result yet — runner will evaluate on interval.
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="options-desk-feeds">
        <div className="options-desk-feed options-desk-feed-card">
          <div className="tile-section-head">
            <h4 className="tile-section-title m-0 text-sm">Algo events</h4>
            <span className="text-xs text-muted">Latest</span>
          </div>
          <div className="event-scroll-panel">
            {algoLogs.length === 0 ? (
              <p className="text-sm text-muted m-0">
                No options algo events yet — cycle skips and IC open/close appear here after the runner ticks.
              </p>
            ) : algoLogs.map((log, i) => (
              <div key={i} className={`event-row ${i === 0 ? 'event-row--latest' : ''}`}>
                <div className="event-row-meta">
                  <span className="event-row-time">{log.time}</span>
                  <span className="event-row-badge">{log.type}</span>
                </div>
                <p className={`m-0 text-sm ${i === 0 ? 'text-main' : 'text-muted'}`}>{log.text}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="options-desk-feed options-desk-feed-card">
          <div className="tile-section-head">
            <h4 className="tile-section-title m-0 text-sm">Algo ledger</h4>
            <span className="text-xs text-muted">Latest</span>
          </div>
          <div className="event-scroll-panel">
            {ledgerEvents.length === 0 ? (
              <p className="text-sm text-muted m-0">No options ledger entries yet.</p>
            ) : ledgerEvents.map((t, i) => {
              const p = (t.payload as Record<string, unknown>) ?? {};
              return (
                <div key={i} className="event-ledger-row">
                  <span className="event-row-time event-ledger-row__time">
                    {formatTime(t.ts as string | number | undefined)}
                  </span>
                  <span className="event-row-badge event-ledger-row__badge">
                    {String(t.event_type ?? '')}
                  </span>
                  <span className="text-main min-w-0 truncate text-sm event-ledger-row__detail">
                    {String(p.underlying ?? p.symbol ?? p.index ?? '')}
                    {p.structure_id != null && (
                      <span className="font-mono text-muted"> {String(p.structure_id)}</span>
                    )}
                    {p.credit != null && (
                      <span className="font-mono text-muted"> credit {formatINR(Number(p.credit))}</span>
                    )}
                    {p.price != null && p.credit == null && (
                      <span className="font-mono text-muted"> @ {formatPrice(Number(p.price))}</span>
                    )}
                    {p.reason != null && (
                      <span className="text-muted"> — {String(p.reason)}</span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}