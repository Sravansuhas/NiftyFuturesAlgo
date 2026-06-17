import { ChevronDown, Gauge, TrendingUp } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { FoMarketMoodSnapshot, FoMoodZone } from '../api/types';

const INDICES = ['NIFTY', 'BANKNIFTY', 'SENSEX'] as const;

const ZONE_CHIP: Record<string, string> = {
  trend_ok: 'fo-zone-chip--profit',
  extended: 'fo-zone-chip--warn',
  ready: 'fo-zone-chip--profit',
  favorable: 'fo-zone-chip--profit',
  neutral: 'fo-zone-chip--muted',
  weak: 'fo-zone-chip--warn',
  cautious: 'fo-zone-chip--warn',
  range_bound: 'fo-zone-chip--warn',
  elevated_chop: 'fo-zone-chip--warn',
  chop_trap: 'fo-zone-chip--loss',
  blocked: 'fo-zone-chip--loss',
  risk_off: 'fo-zone-chip--loss',
};

const ZONE_COLOR: Record<string, string> = {
  trend_ok: 'var(--intent-profit)',
  extended: 'var(--intent-warn)',
  ready: 'var(--intent-profit)',
  favorable: 'var(--intent-profit)',
  neutral: 'var(--text-muted)',
  weak: 'var(--intent-warn)',
  cautious: 'var(--intent-warn)',
  range_bound: 'var(--intent-warn)',
  elevated_chop: 'var(--intent-warn)',
  chop_trap: 'var(--intent-loss)',
  blocked: 'var(--intent-loss)',
  risk_off: 'var(--intent-loss)',
};

function formatZone(zone?: FoMoodZone): string {
  if (!zone) return '—';
  return zone.replace(/_/g, ' ');
}

type MacroFormatOpts = {
  available?: boolean;
  decimals?: number;
  suffix?: string;
  signed?: boolean;
};

function formatMacroValue(
  value: number | null | undefined,
  opts: MacroFormatOpts = {},
): string {
  const { available, decimals = 0, suffix = '', signed = false } = opts;
  if (available === false) return '—';
  if (value == null || Number.isNaN(value)) return '—';
  const n = Number(value);
  const text = n.toFixed(decimals);
  const signedText = signed && n > 0 ? `+${text}` : text;
  return `${signedText}${suffix}`;
}

function scoreColor(score: number): string {
  if (score >= 65) return 'var(--intent-profit)';
  if (score >= 40) return 'var(--brand-primary)';
  if (score >= 25) return 'var(--intent-warn)';
  return 'var(--intent-loss)';
}

function polar(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 180) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, r: number, start: number, end: number) {
  const s = polar(cx, cy, r, start);
  const e = polar(cx, cy, r, end);
  const large = end - start > 180 ? 1 : 0;
  return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 1 ${e.x} ${e.y}`;
}

interface GaugeProps {
  label: string;
  value: number;
  zone?: FoMoodZone;
  marketClosed?: boolean;
}

function MoodGauge({ label, value, zone, marketClosed }: GaugeProps) {
  const showUnavailable = marketClosed && value === 0;
  const clamped = Math.max(0, Math.min(100, value));
  const fillEnd = showUnavailable ? 0 : (clamped / 100) * 180;
  const stroke = zone ? (ZONE_COLOR[zone] ?? scoreColor(clamped)) : scoreColor(clamped);
  const cx = 70;
  const cy = 62;
  const r = 48;

  return (
    <div className="fo-mood-gauge">
      <svg className="fo-mood-gauge__arc" viewBox="0 0 140 78" aria-hidden>
        <path
          d={arcPath(cx, cy, r, 0, 180)}
          fill="none"
          stroke="var(--border-dim)"
          strokeWidth="8"
          strokeLinecap="round"
        />
        <path
          d={arcPath(cx, cy, r, 0, fillEnd)}
          fill="none"
          stroke={stroke}
          strokeWidth="8"
          strokeLinecap="round"
        />
      </svg>
      <span className="fo-mood-gauge__value font-mono">
        {showUnavailable ? '—' : Math.round(clamped)}
      </span>
      <span className="fo-mood-gauge__label">{label}</span>
      {zone && (
        <span className={`fo-zone-chip ${ZONE_CHIP[zone] ?? 'fo-zone-chip--muted'}`}>
          {formatZone(zone)}
        </span>
      )}
    </div>
  );
}

interface Props {
  mood?: FoMarketMoodSnapshot | null;
  variant?: 'full' | 'rail';
  marketOpen?: boolean;
  sessionStatus?: string;
}

export default function FoMoodPanel({
  mood: streamMood,
  variant = 'full',
  marketOpen,
  sessionStatus,
}: Props) {
  const [polled, setPolled] = useState<FoMarketMoodSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [componentsOpen, setComponentsOpen] = useState(false);

  useEffect(() => {
    const streamFresh = (snap?: FoMarketMoodSnapshot | null) => {
      if (!snap?.timestamp) return false;
      const ageMs = Date.now() - new Date(snap.timestamp).getTime();
      return ageMs >= 0 && ageMs < 45_000;
    };

    const load = () => {
      if (streamFresh(streamMood)) return;
      api.getFoMarketMood()
        .then((r) => {
          setPolled(r);
          setLoadError(r.error ?? null);
        })
        .catch((err: Error) => {
          setLoadError(err.message || 'Failed to load F&O mood');
        });
    };

    load();
    const pollMs = streamFresh(streamMood) ? 90_000 : 45_000;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [streamMood]);

  const mood = streamMood ?? polled;
  const isRail = variant === 'rail';
  const tapeMood = mood?.tape_mood ?? 0;
  const tradeability = mood?.tradeability ?? 0;
  const divergence = mood?.divergence ?? Math.abs(tapeMood - tradeability);
  const mismatch = Boolean(mood?.mismatch) || divergence > 25;

  const indexRows = useMemo(() => {
    const map = mood?.indices ?? {};
    return INDICES.map((sym) => ({ sym, row: map[sym] }));
  }, [mood?.indices]);

  /** Per-index rows are redundant when every symbol shows the same FLAT/ranging/chop read. */
  const showIndexRows = useMemo(() => {
    if (isRail) return false;
    if (indexRows.length === 0) return false;
    const signatures = indexRows.map(({ row }) => [
      row?.proposed ?? 'FLAT',
      row?.algo_trend ?? row?.trend ?? '',
      row?.chop_score != null ? (row.chop_score * 100).toFixed(0) : '',
      row?.brother_bias ?? '',
      row?.tape_mood != null ? Math.round(row.tape_mood) : '',
    ].join('|'));
    const hasSignal = indexRows.some(
      ({ row }) => row?.proposed === 'LONG' || row?.proposed === 'SHORT',
    );
    const hasSheetBias = indexRows.some(
      ({ row }) => row?.brother_bias && row.brother_bias !== 'none' && row.brother_bias !== 'balanced',
    );
    return hasSignal || hasSheetBias || new Set(signatures).size > 1;
  }, [indexRows, isRail]);

  const components = mood?.components ?? [];
  const macro = mood?.macro;
  const vix = macro?.vix;
  const fii = macro?.fii_dii;

  const marketClosed = marketOpen === false;

  const moodBody = mood ? (
    <>
      <div className="fo-mood-gauges">
        <MoodGauge label="Tape mood" value={tapeMood} zone={mood.tape_zone} marketClosed={marketClosed} />
        <MoodGauge
          label="Tradeability"
          value={tradeability}
          zone={mood.tradeability_zone}
          marketClosed={marketClosed}
        />
      </div>

      {mismatch && !isRail && (
        <div className="fo-mood-mismatch" role="status">
          <strong>Tape vs tradeability mismatch</strong>
          <span className="text-muted">
            {' '}— {Math.round(divergence)} pt gap (tape {Math.round(tapeMood)} · trade {Math.round(tradeability)}).
            {mood.mismatch_detail ? ` ${mood.mismatch_detail}` : ' Breakout tape may not match algo entry quality.'}
          </span>
        </div>
      )}

      <div className="fo-mood-summaries">
        {mood.human_summary && !isRail && (
          <div className="fo-mood-summary fo-mood-summary--human">
            <span className="fo-mood-summary__tag">Desk read</span>
            <p className="m-0">{mood.human_summary}</p>
          </div>
        )}
        {mood.algo_summary && (
          <div className="fo-mood-summary fo-mood-summary--algo">
            <span className="fo-mood-summary__tag">
              <TrendingUp size={12} /> Algo
            </span>
            <p className={`m-0 ${isRail ? 'fo-mood-summary__algo-text' : 'font-mono text-sm'}`}>
              {mood.algo_summary}
            </p>
          </div>
        )}
      </div>

      <div className="fo-mood-macro">
        <div className="fo-mood-macro__cell">
          <span className="fo-mood-macro__label">VIX</span>
          <span className="fo-mood-macro__value font-mono">
            {formatMacroValue(vix?.level, { available: vix?.available, decimals: 1 })}
            {vix?.available && vix.zone ? ` (${vix.zone})` : ''}
            {vix?.available && vix.change_pct != null
              ? ` · ${vix.change_pct >= 0 ? '+' : ''}${vix.change_pct.toFixed(1)}%`
              : ''}
          </span>
        </div>
        <div className="fo-mood-macro__cell">
          <span className="fo-mood-macro__label">FII net</span>
          <span className="fo-mood-macro__value font-mono">
            {formatMacroValue(fii?.fii_net_crores, { available: fii?.available, suffix: ' Cr', signed: true })}
          </span>
          {fii?.trade_date && (
            <span className="fo-mood-macro__date font-mono">as of {fii.trade_date}</span>
          )}
        </div>
        <div className="fo-mood-macro__cell">
          <span className="fo-mood-macro__label">DII net</span>
          <span className="fo-mood-macro__value font-mono">
            {formatMacroValue(fii?.dii_net_crores, { available: fii?.available, suffix: ' Cr', signed: true })}
          </span>
          {fii?.available && fii.flow_bias && (
            <span className="fo-mood-macro__date">{fii.flow_bias.replace(/_/g, ' ')}</span>
          )}
        </div>
      </div>

      {showIndexRows && (
        <div className="fo-mood-indexes">
          {indexRows.map(({ sym, row }) => (
            <div key={sym} className="fo-mood-index-row">
              <div className="fo-mood-index-head">
                <span className="fo-mood-index-sym">{sym}</span>
                <span
                  className={`fo-mood-index-proposed ${
                    row?.proposed === 'LONG'
                      ? 'text-profit'
                      : row?.proposed === 'SHORT'
                        ? 'text-loss'
                        : 'text-muted'
                  }`}
                >
                  {row?.proposed ?? 'FLAT'}
                </span>
              </div>
              <p className="fo-mood-index-detail m-0">
                algo {row?.algo_trend ?? row?.trend ?? '—'}
                {row?.chop_score != null ? ` · chop ${(row.chop_score * 100).toFixed(0)}` : ''}
                {row?.brother_bias && row.brother_bias !== 'none' ? ` · sheet ${row.brother_bias}` : ''}
              </p>
              {(row?.tape_mood != null || row?.tradeability != null) && (
                <p className="fo-mood-index-scores m-0 font-mono">
                  {row?.tape_mood != null ? `tape ${Math.round(row.tape_mood)}` : ''}
                  {row?.tape_mood != null && row?.tradeability != null ? ' · ' : ''}
                  {row?.tradeability != null ? `trade ${Math.round(row.tradeability)}` : ''}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {components.length > 0 && !isRail && (
        <div className="fo-mood-components">
          <button
            type="button"
            className="fo-mood-components__toggle"
            onClick={() => setComponentsOpen((o) => !o)}
            aria-expanded={componentsOpen}
          >
            <span>Component breakdown ({components.length})</span>
            <ChevronDown
              size={14}
              className={`fo-mood-components__chevron ${componentsOpen ? 'fo-mood-components__chevron--open' : ''}`}
            />
          </button>
          {componentsOpen && (
            <div className="fo-mood-components__list">
              {components.map((c) => (
                <div key={c.id} className="fo-mood-component-row">
                  <div className="fo-mood-component-head">
                    <span className="fo-mood-component-label">{c.label}</span>
                    <span className="fo-mood-component-score font-mono">{Math.round(c.score)}</span>
                  </div>
                  <div className="risk-meter risk-meter--thin">
                    <div
                      className="risk-meter-fill"
                      style={{
                        width: `${Math.max(0, Math.min(100, c.score))}%`,
                        backgroundColor: c.zone
                          ? (ZONE_COLOR[c.zone] ?? scoreColor(c.score))
                          : scoreColor(c.score),
                      }}
                    />
                  </div>
                  {(c.contribution != null || c.weight != null || c.detail) && (
                    <p className="fo-mood-component-meta m-0">
                      {c.weight != null ? `wt ${(c.weight * 100).toFixed(0)}%` : ''}
                      {c.contribution != null ? `${c.weight != null ? ' · ' : ''}+${c.contribution.toFixed(1)}` : ''}
                      {c.detail ? `${c.weight != null || c.contribution != null ? ' · ' : ''}${c.detail}` : ''}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  ) : null;

  return (
    <div className={`bento-tile bento-tile--auto fo-mood-panel${isRail ? ' fo-mood-panel--rail' : ''}`}>
      <div className="tile-section-head fo-mood-panel__header">
        <h3 className="tile-section-title m-0">
          <Gauge size={16} /> Market F&amp;O Mood
        </h3>
        {mood?.timestamp && (
          <span className="text-xs text-muted font-mono">
            {new Date(mood.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      {marketClosed && (
        <div className="session-status-banner session-status-banner--closed" role="status">
          <strong>Market closed</strong>
          {' '}— macro data (VIX/FII) still updates; tape mood needs live session
          {sessionStatus && (
            <span className="session-status-banner__tag font-mono">{sessionStatus}</span>
          )}
        </div>
      )}

      {!mood && loadError && (
        <p className="text-sm text-muted m-0">Mood unavailable — {loadError}</p>
      )}

      {isRail ? (
        <div className="fo-mood-panel__scroll">{moodBody}</div>
      ) : (
        moodBody
      )}
    </div>
  );
}