import { Sparkles, ShieldAlert, Calculator } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { api } from '../api/client';
import FoMoodPanel from '../components/FoMoodPanel';
import PosturePanel from '../components/PosturePanel';
import type { LiveSnapshot, PerSymbolStatus, RecentExecution, StatusStreamPayload, SystemStatus } from '../api/types';
import { eventLabel, eventText, formatINR, formatPrice, formatTime } from '../utils/format';
import { computeDailyPnlBreakdown } from '../utils/foCosts';

interface OutletContext {
  status: SystemStatus | null;
  stream: StatusStreamPayload | null;
}

const INDICES = ['NIFTY', 'BANKNIFTY', 'SENSEX'] as const;
export default function Dashboard() {
  const { status, stream } = useOutletContext<OutletContext>();
  const [riskConfig, setRiskConfig] = useState<{
    max_trades: number;
    capital: number;
    lot_size: number;
    max_order_quantity: number;
    max_lots: number;
    risk_per_trade_pct: number;
    trade_budget?: import('../api/types').TradeBudgetSummary;
  }>({ max_trades: 3, capital: 1_000_000, lot_size: 65, max_order_quantity: 195, max_lots: 3, risk_per_trade_pct: 0.005 });
  const [recentTrades, setRecentTrades] = useState<Array<Record<string, unknown>>>([]);
  const [calcRisk, setCalcRisk] = useState(0.5);
  const [calcSL, setCalcSL] = useState(25);

  useEffect(() => {
    const load = () => {
      api.getRiskConfig().then((r) => {
        if (r.loaded) {
          setRiskConfig({
            max_trades: r.max_trades_per_symbol ?? r.max_trades_per_day,
            capital: r.capital,
            lot_size: r.lot_size ?? 65,
            max_order_quantity: r.max_order_quantity ?? 195,
            max_lots: r.max_lots ?? 3,
            risk_per_trade_pct: r.risk_per_trade_pct ?? 0.005,
            trade_budget: r.trade_budget,
          });
          setCalcRisk(r.risk_per_trade_pct * 100);
        }
      }).catch(() => {});
      api.getTrades(20).then((r) => setRecentTrades(r.trades ?? [])).catch(() => {});
    };
    load();
    const id = setInterval(load, 12000);
    return () => clearInterval(id);
  }, []);

  const snapshots = stream?.live_snapshots ?? status?.live_snapshots ?? {};
  const perSymbol = stream?.per_symbol_status ?? status?.per_symbol_status ?? {};
  const recentExec = stream?.recent_execution ?? status?.recent_execution ?? [];
  const hasLiveData = Object.keys(snapshots).length > 0;
  const engineOnline = status && !status.error;
  const tradeBudget = status?.trade_budget ?? riskConfig.trade_budget;
  const foGuards = status?.fo_guards;
  const portfolioTrades = tradeBudget?.portfolio_trades ?? status?.trades_today ?? 0;
  const portfolioCap = tradeBudget?.portfolio_cap ?? (riskConfig.max_trades * 3);
  const tradesToday = portfolioTrades;
  const maxTrades = portfolioCap;
  const futuresPnl = status?.daily_pnl ?? 0;
  const capital = status?.capital ?? riskConfig.capital;

  const indexCards = INDICES.map((sym) => {
    const snap = snapshots[sym] as LiveSnapshot | undefined;
    const pos = perSymbol[sym] as PerSymbolStatus | undefined;
    const contract = snap?.contract ?? snap?.symbol ?? `${sym} FUT`;
    return {
      name: sym,
      contract,
      contractLabel: contract.toUpperCase().endsWith('FUT') ? `${contract} LTP` : `${contract} FUT LTP`,
      price: snap?.ltp,
      spot: snap?.spot_ltp,
      basis: snap?.spot_basis,
      proposed: snap?.proposed ?? 'FLAT',
      regime: snap?.regime?.volatility ?? 'normal',
      source: snap?.data_source ?? '—',
      position: pos?.position ?? 0,
      pnl: pos?.live_unrealized_pnl ?? pos?.daily_pnl ?? 0,
    };
  });

  const anySimulated = indexCards.some((c) => c.source === 'SIMULATED');
  const allLive = indexCards.length > 0 && indexCards.every((c) => c.source === 'WS' || c.source === 'REAL');
  const marketOpen = status?.market?.is_market_open ?? false;
  const showSimulatedWarning = anySimulated && !(marketOpen && allLive);
  const feedBadge = anySimulated ? 'SIMULATED' : allLive ? 'LIVE' : hasLiveData ? 'DEGRADED' : 'OFFLINE';
  const feedBadgeClasses = anySimulated
    ? 'bg-intent-warn-dim text-warn border border-[rgba(245,158,11,0.3)]'
    : allLive
      ? 'bg-intent-profit-dim text-profit border border-[rgba(16,185,129,0.3)]'
      : 'bg-surface-elevated text-muted border border-dim';

  const positions = INDICES.flatMap((sym) => {
    const pos = perSymbol[sym] as PerSymbolStatus | undefined;
    const snap = snapshots[sym] as LiveSnapshot | undefined;
    if (!pos || pos.position === 0) return [];
    return [{
      sym,
      instrument: snap?.symbol ?? `${sym} FUT`,
      qty: pos.position,
      avg: pos.avg_price,
      ltp: snap?.ltp ?? 0,
      target: snap?.target ?? 0,
      stopLoss: snap?.stop_loss ?? 0,
      pnl: pos.live_unrealized_pnl ?? pos.daily_pnl ?? 0,
      regime: snap?.regime?.volatility ?? 'normal',
      confidence: snap?.confidence ?? 0,
    }];
  });

  const snapshotLogs = INDICES.flatMap((sym) => {
    const snap = snapshots[sym] as LiveSnapshot | undefined;
    if (!snap?.gate_summary) return [];
    return [{
      time: snap.last_update ?? 'Live',
      type: 'SCAN',
      text: `${sym}: ${snap.gate_summary}`,
    }];
  });

  const aiLogs = (recentExec.length > 0 ? recentExec : []).slice(0, 8).map((e: RecentExecution) => ({
    time: formatTime(e.ts),
    type: eventLabel(e.type),
    text: eventText(e),
  }));

  const displayLogs = (aiLogs.length > 0 ? aiLogs : snapshotLogs).filter(
    (log, i, arr) => arr.findIndex((x) => x.text === log.text) === i,
  ).slice(0, 5);

  const ledgerEvents = recentTrades.slice(0, 5);

  const pnlBreakdown = computeDailyPnlBreakdown(
    futuresPnl,
    status?.options_mtm,
    perSymbol as Record<string, import('../api/types').PerSymbolStatus>,
    riskConfig.lot_size,
  );
  const hasOptionsMtm = (status?.options_mtm?.available && (status?.options_mtm?.legs ?? 0) > 0) ?? false;
  const isPaper = status?.mode === 'PAPER';
  const pnlModeLabel = isPaper ? 'Paper' : 'Live';

  const riskAmountInr = capital * (calcRisk / 100);
  const riskPerLotInr = calcSL * riskConfig.lot_size;
  const safeLots = calcSL > 0 && riskPerLotInr > 0
    ? Math.floor(riskAmountInr / riskPerLotInr)
    : 0;
  const rawSafeQty = safeLots * riskConfig.lot_size;
  const engineCapped = rawSafeQty > riskConfig.max_order_quantity;
  const safeQty = Math.min(rawSafeQty, riskConfig.max_order_quantity);

  const tradePct = Math.min(100, (tradesToday / Math.max(1, maxTrades)) * 100);
  const guardBlocked = foGuards?.any_blocked ?? false;
  const guardAtLimit = tradesToday >= maxTrades;
  const guardStatus = guardBlocked ? 'blocked' : guardAtLimit ? 'limit' : 'ok';
  const guardStatusLabel = guardBlocked ? 'Blocked' : guardAtLimit ? 'At limit' : 'Within budget';
  const guardStatusClass = guardBlocked ? 'badge-loss' : guardAtLimit ? 'badge-warn' : 'badge-profit';

  const activeGuards = INDICES.flatMap((sym) => {
    const g = foGuards?.symbols?.[sym];
    if (!g?.active_guards?.length) return [];
    return g.active_guards.slice(0, 2).map((rule) => ({
      sym,
      id: `${sym}-${rule.id}`,
      label: rule.label,
      blocked: !g.allowed,
    }));
  }).slice(0, 5);

  return (
    <div className="bento-grid dashboard-grid">

      {/* ── Tier 0: compact system alerts (no tile stretch) ── */}
      {!engineOnline && (
        <div className="dashboard-alert dashboard-alert--error">
          <strong>No live data.</strong> Start <code>python run.py --dev</code> then refresh.
        </div>
      )}

      {engineOnline && !hasLiveData && (
        <div className="dashboard-alert dashboard-alert--info">
          Engine connected — waiting for SSE. Same host as <code>run.py</code> → <code>/ui/dashboard</code>.
        </div>
      )}

      {showSimulatedWarning && (
        <div className="dashboard-alert dashboard-alert--warn">
          <strong>Simulated feed.</strong> Run <code>python generate_token.py</code>, restart <code>run.py</code>.
          Compare FUT contract on Kite (e.g. NIFTY26JUNFUT), not the index chart.
        </div>
      )}

      {!status?.token_valid && engineOnline && (
        <div className="dashboard-alert dashboard-alert--error">
          <strong>Token expired.</strong> <code>python generate_token.py</code> before market open.
        </div>
      )}

      {/* ── Tier 0.5: F&O session mood (above market intel) ── */}
      <FoMoodPanel mood={stream?.fo_mood ?? status?.fo_mood} />

      {/* ── Tier 1: P&L + live market (quant desk primary) ── */}
      <div className="bento-tile bento-tile--pair-lg" style={{ gridColumn: 'span 4' }}>
        <div className="tile-section-head">
          <h3 className="tile-section-title m-0">Daily P&L</h3>
          <span className="badge badge-muted normal-case tracking-normal">{pnlModeLabel}</span>
        </div>

        <div className="pnl-hero">
          <span className="pnl-hero-label">Net after taxes (est.)</span>
          <span className={`pnl-hero-value ${pnlBreakdown.combinedNet >= 0 ? 'text-profit' : 'text-loss'}`}>
            {formatINR(pnlBreakdown.combinedNet, true)}
          </span>
        </div>

        <div className="pnl-breakdown pnl-breakdown--fill">
          <div className="metric-row">
            <span className="metric-row-label">Futures MTM (gross)</span>
            <span className={`metric-row-value ${pnlBreakdown.futuresGross >= 0 ? 'text-profit' : 'text-loss'}`}>
              {formatINR(pnlBreakdown.futuresGross, true)}
            </span>
          </div>
          <div className="metric-row">
            <span className="metric-row-label">
              Futures statutory
              {pnlBreakdown.openLegs > 0 ? ` (${pnlBreakdown.openLegs} open)` : ''}
            </span>
            <span className="metric-row-value text-loss">−{formatINR(pnlBreakdown.futuresStatutory)}</span>
          </div>
          {hasOptionsMtm && (
            <>
              <div className="metric-row">
                <span className="metric-row-label">Options MTM (gross)</span>
                <span className={`metric-row-value ${pnlBreakdown.optionsGross >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {formatINR(pnlBreakdown.optionsGross, true)}
                </span>
              </div>
              {pnlBreakdown.optionsStatutory > 0 && (
                <div className="metric-row">
                  <span className="metric-row-label">Options statutory (STT 0.15%)</span>
                  <span className="metric-row-value text-loss">−{formatINR(pnlBreakdown.optionsStatutory)}</span>
                </div>
              )}
              <div className="metric-row">
                <span className="metric-row-label">Options MTM (net)</span>
                <span className={`metric-row-value ${pnlBreakdown.optionsNet >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {formatINR(pnlBreakdown.optionsNet, true)}
                </span>
              </div>
            </>
          )}
          <div className="metric-divider" />
          <div className="metric-row">
            <span className="metric-row-label font-semibold text-main">Combined gross</span>
            <span className={`metric-row-value ${pnlBreakdown.combinedGross >= 0 ? 'text-profit' : 'text-loss'}`}>
              {formatINR(pnlBreakdown.combinedGross, true)}
            </span>
          </div>
          <div className="metric-row">
            <span className="metric-row-label">Max drawdown</span>
            <span className="metric-row-value text-loss">{status?.max_drawdown ?? 0}%</span>
          </div>
          <div className="metric-row">
            <span className="metric-row-label">Equity</span>
            <span className="metric-row-value">{formatINR(status?.current_equity ?? capital)}</span>
          </div>
        </div>
      </div>

      <div className="bento-tile bento-tile--pair-lg" style={{ gridColumn: 'span 8' }}>
        <div className="tile-section-head">
          <h3 className="tile-section-title m-0">Market Intelligence</h3>
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${feedBadgeClasses}`}>{feedBadge}</span>
        </div>
        <p className="text-sm text-muted m-0">
          Front-month FUT LTP — match the same symbol in Kite Marketwatch.
        </p>

        <div className="tile-body-fill">
          <div className="market-intel-indexes">
            {indexCards.map((idx) => (
              <div key={idx.name} className="index-mini-card">
                <span className="text-sm text-muted font-medium">{idx.name} FUT</span>
                <span className="font-mono text-xl font-bold text-high truncate">{formatPrice(idx.price)}</span>
                <span className="text-xs text-muted truncate" title={idx.contractLabel}>{idx.contractLabel}</span>
                <span className="text-xs text-muted">
                  {idx.spot != null
                    ? `Spot ${formatPrice(idx.spot)}${idx.basis != null ? ` (${idx.basis >= 0 ? '+' : ''}${idx.basis.toFixed(1)})` : ''}`
                    : 'Spot —'}
                </span>
                <span className={`font-mono text-xs font-semibold mt-auto ${idx.proposed === 'LONG' ? 'text-profit' : idx.proposed === 'SHORT' ? 'text-loss' : 'text-muted'}`}>
                  {idx.proposed} · {idx.regime} · {idx.source}
                </span>
              </div>
            ))}
          </div>

          <div className="flex gap-1 h-6 w-full rounded overflow-hidden flex-shrink-0">
            {INDICES.map((sym, i) => {
              const pnl = (perSymbol[sym] as PerSymbolStatus | undefined)?.daily_pnl ?? 0;
              const up = pnl >= 0;
              return (
                <div
                  key={sym}
                  className={`flex items-center justify-center text-xs font-bold ${up ? 'bg-intent-profit text-high' : 'bg-intent-loss text-high'}`}
                  style={{ flex: i === 0 ? 4 : i === 1 ? 3 : 2 }}
                >
                  {sym}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── Tier 2: open book ── */}
      <div className="bento-tile bento-tile--auto" style={{ gridColumn: 'span 12' }}>
        <div className="tile-section-head">
          <h3 className="tile-section-title m-0">Active Futures Positions</h3>
          <span className="text-xs text-muted">{positions.length} open</span>
        </div>

        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                {['Instrument', 'Qty', 'Avg', 'LTP', 'Target', 'Stop Loss', 'Regime', 'Conf.', 'P&L'].map((h) => (
                  <th key={h} className={h === 'P&L' ? 'text-right' : 'text-left'}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.length === 0 ? (
                <tr>
                  <td colSpan={9} className="text-muted py-4">Flat — scanning for breakouts.</td>
                </tr>
              ) : positions.map((pos) => (
                <tr key={pos.sym} className="group">
                  <td className="font-semibold truncate" style={{ maxWidth: '10rem' }} title={pos.instrument}>{pos.instrument}</td>
                  <td className={`font-mono ${pos.qty > 0 ? 'text-profit' : 'text-loss'}`}>{pos.qty}</td>
                  <td className="font-mono text-muted">{formatPrice(pos.avg)}</td>
                  <td className="font-mono text-high">{formatPrice(pos.ltp)}</td>
                  <td className="font-mono text-profit">{formatPrice(pos.target)}</td>
                  <td className="font-mono text-loss">{formatPrice(pos.stopLoss)}</td>
                  <td className="text-muted capitalize">{pos.regime}</td>
                  <td className="font-mono text-muted">{(pos.confidence * 100).toFixed(0)}%</td>
                  <td className={`font-mono text-right font-bold ${pos.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>{formatINR(pos.pnl, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Tier 3: session posture ── */}
      <PosturePanel status={status} stream={stream} />

      {/* ── Tier 4: trade budget ── */}
      <div
        className={`bento-tile bento-tile--auto guard-card guard-card--${guardStatus}`}
        style={{ gridColumn: 'span 12' }}
      >
        <div className="tile-section-head">
          <h3 className="tile-section-title m-0">
            <ShieldAlert size={16} /> Trade Budget
          </h3>
          <span className={`badge ${guardStatusClass}`}>{guardStatusLabel}</span>
        </div>

        <div className="guard-hero">
          <div className="guard-hero-metric">
            <span className="guard-hero-value font-mono">{tradesToday}</span>
            <span className="guard-hero-cap text-muted">/ {maxTrades}</span>
          </div>
          <span className="guard-hero-label">Portfolio trades today</span>
        </div>

        <div className="guard-portfolio-meter">
          <div className="risk-meter">
            <div
              className={`risk-meter-fill ${guardAtLimit ? 'bg-intent-loss' : guardBlocked ? 'bg-intent-warn' : 'bg-brand-primary'}`}
              style={{ width: `${tradePct}%` }}
            />
          </div>
          <span className="text-xs text-muted">{tradePct.toFixed(0)}% of daily cap used</span>
        </div>

        <div className="guard-index-list guard-index-list--horizontal">
          {INDICES.map((sym) => {
            const b = tradeBudget?.per_symbol?.[sym];
            const used = b?.trades_used ?? perSymbol[sym]?.daily_trades ?? 0;
            const cap = b?.effective_cap ?? riskConfig.max_trades;
            const pct = Math.min(100, (used / Math.max(1, cap)) * 100);
            const bonus = b?.bonus_granted ? `+${b.bonus_available}` : null;
            return (
              <div key={sym} className="guard-index-row">
                <div className="guard-index-head">
                  <span className="guard-index-sym">{sym}</span>
                  <span className="guard-index-count font-mono">
                    {used}/{cap}
                    {bonus && <span className="text-brand text-xs ml-1">({bonus})</span>}
                  </span>
                </div>
                <div className="risk-meter risk-meter--thin">
                  <div
                    className={`risk-meter-fill ${used >= cap ? 'bg-intent-loss' : 'bg-intent-profit'}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        {tradeBudget?.adaptive_enabled && (
          <p className="guard-footnote text-xs text-muted m-0">
            Adaptive mode — bonus trade only when regime score clears quality gate.
          </p>
        )}

        {activeGuards.length > 0 && (
          <div className="guard-rules">
            <span className="stat-strip-label">Active F&O rules</span>
            <div className="guard-chip-row">
              {activeGuards.map((g) => (
                <span key={g.id} className={`guard-chip ${g.blocked ? 'guard-chip--blocked' : ''}`}>
                  <span className="guard-chip-sym">{g.sym}</span>
                  {g.label}
                </span>
              ))}
            </div>
          </div>
        )}

        {guardBlocked && foGuards?.portfolio_block_reason && (
          <p className="guard-alert text-xs text-loss m-0">{foGuards.portfolio_block_reason}</p>
        )}
      </div>

      {/* ── Tier 5: live event feed (latest 5, inner scroll) ── */}
      <div className="bento-tile bento-tile--feed" style={{ gridColumn: 'span 6' }}>
        <div className="tile-section-head">
          <h3 className="tile-section-title text-brand m-0">
            <Sparkles size={16} /> Execution Rationale
          </h3>
          <span className="text-xs text-muted">Latest 5</span>
        </div>

        <div className="event-scroll-panel">
          {displayLogs.length === 0 ? (
            <p className="text-sm text-muted m-0">
              {hasLiveData
                ? 'Scanning — no entry signal yet.'
                : 'Waiting for engine stream.'}
            </p>
          ) : displayLogs.map((log, i) => (
            <div key={i} className={`event-row ${i === 0 ? 'event-row--latest' : ''}`}>
              <div className="event-row-meta">
                <span className="event-row-time">{log.time}</span>
                <span className="event-row-badge">{log.type}</span>
              </div>
              <p className={`m-0 ${i === 0 ? 'text-main' : 'text-muted'}`}>{log.text}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="bento-tile bento-tile--feed" style={{ gridColumn: 'span 6' }}>
        <div className="tile-section-head">
          <h3 className="tile-section-title m-0">Trade Ledger (Live)</h3>
          <span className="text-xs text-muted">Latest 5</span>
        </div>

        <div className="event-scroll-panel">
          {ledgerEvents.length === 0 ? (
            <p className="text-sm text-muted m-0">No ledger events yet.</p>
          ) : ledgerEvents.map((t, i) => {
            const p = (t.payload as Record<string, unknown>) ?? {};
            return (
              <div key={i} className="event-ledger-row">
                <span className="event-row-time" style={{ minWidth: '4.5rem' }}>{formatTime(t.ts as string | number | undefined)}</span>
                <span className="event-row-badge" style={{ minWidth: '5.5rem', textAlign: 'center' }}>{String(t.event_type ?? '')}</span>
                <span className="text-main min-w-0 truncate">
                  {String(p.index ?? p.symbol ?? p.side ?? '')}
                  {p.price != null && (
                    <span className="font-mono text-muted"> @ {formatPrice(Number(p.price))}</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Tier 6: reference tools (bottom) ── */}
      <div className="bento-tile bento-tile--auto calc-card" style={{ gridColumn: 'span 12' }}>
        <div className="tile-section-head">
          <h3 className="tile-section-title m-0">
            <Calculator size={16} /> Position Sizer
          </h3>
          <button
            type="button"
            className="text-xs text-brand hover:underline bg-transparent border-0 cursor-pointer p-0"
            onClick={() => setCalcRisk(riskConfig.risk_per_trade_pct * 100)}
          >
            Reset to engine {(riskConfig.risk_per_trade_pct * 100).toFixed(2)}%
          </button>
        </div>
        <p className="text-xs text-muted m-0">Reference only — RiskGatekeeper enforces live limits.</p>

        <div className="calc-form">
          <label className="calc-field">
            <span className="calc-field-label">Risk per trade</span>
            <div className="calc-input-wrap">
              <input
                type="number"
                value={calcRisk}
                step={0.1}
                min={0.1}
                max={5}
                onChange={(e) => setCalcRisk(Number(e.target.value))}
                className="input-field input-field--compact calc-input"
              />
              <span className="calc-suffix">%</span>
            </div>
            <span className="calc-hint">{formatINR(riskAmountInr)} at risk</span>
          </label>
          <label className="calc-field">
            <span className="calc-field-label">Stop loss</span>
            <div className="calc-input-wrap">
              <input
                type="number"
                value={calcSL}
                min={1}
                onChange={(e) => setCalcSL(Number(e.target.value))}
                className="input-field input-field--compact calc-input"
              />
              <span className="calc-suffix">pts</span>
            </div>
            <span className="calc-hint">{formatINR(riskPerLotInr)}/lot · lot {riskConfig.lot_size}</span>
          </label>
        </div>

        <div className="calc-result">
          <div className="calc-result-main">
            <span className="calc-result-label">Suggested size</span>
            <span className="calc-result-qty font-mono">{safeQty} qty</span>
            <span className="calc-result-sub text-muted">
              {safeLots} lot{safeLots !== 1 ? 's' : ''} × {riskConfig.lot_size}
              {engineCapped && (
                <span className="text-warn"> · capped at {riskConfig.max_order_quantity}</span>
              )}
            </span>
          </div>
          <div className="calc-result-meta">
            <div className="calc-meta-cell">
              <span className="calc-meta-label">Capital</span>
              <span className="calc-meta-value font-mono">{formatINR(capital)}</span>
            </div>
            <div className="calc-meta-cell">
              <span className="calc-meta-label">Max lots</span>
              <span className="calc-meta-value font-mono">{riskConfig.max_lots}</span>
            </div>
            <div className="calc-meta-cell">
              <span className="calc-meta-label">Notional / lot</span>
              <span className="calc-meta-value font-mono">
                {snapshots.NIFTY
                  ? formatINR((snapshots.NIFTY as LiveSnapshot).ltp! * riskConfig.lot_size)
                  : '—'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}