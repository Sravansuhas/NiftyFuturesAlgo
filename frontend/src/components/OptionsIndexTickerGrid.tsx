import { Radio } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { OptionsDeskTickerLeg, OptionsDeskTickerRow, OptionsDeskTickers } from '../api/types';
import { formatPrice } from '../utils/format';

const INDEX_ROWS = [
  { key: 'SENSEX', label: 'SENSEX', headerClass: 'index-header--sensex' },
  { key: 'NIFTY', label: 'NIFTY 50', headerClass: 'index-header--nifty' },
  { key: 'BANKNIFTY', label: 'BANK NIFTY', headerClass: 'index-header--banknifty' },
] as const;

function LiveDot({ live }: { live?: boolean }) {
  return (
    <span
      className={`options-live-dot ${live ? 'options-live-dot--on' : ''}`}
      title={live ? 'Live feed' : 'Awaiting feed'}
    />
  );
}

function formatChange(value: number | null | undefined, pct?: number | null): string {
  if (value == null || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  const base = `${sign}${value.toFixed(2)}`;
  if (pct != null && !Number.isNaN(pct)) {
    const pctSign = pct > 0 ? '+' : '';
    return `${base} (${pctSign}${pct.toFixed(2)}%)`;
  }
  return base;
}

function resolveChange(
  leg?: OptionsDeskTickerLeg | null,
): { change: number | null; changePct: number | null } {
  if (!leg) return { change: null, changePct: null };
  if (leg.change != null) {
    return { change: leg.change, changePct: leg.change_pct ?? null };
  }
  if (leg.ltp != null && leg.prev_close != null) {
    const change = leg.ltp - leg.prev_close;
    const changePct = leg.prev_close !== 0 ? (change / leg.prev_close) * 100 : null;
    return { change, changePct };
  }
  return { change: null, changePct: null };
}

function TickerCard({
  leg,
  variant,
  rowExpiry,
  rowLive,
}: {
  leg?: OptionsDeskTickerLeg | null;
  variant: 'ce' | 'pe';
  rowExpiry?: string | null;
  rowLive?: boolean;
}) {
  const { change, changePct } = resolveChange(leg);
  const changeClass =
    change == null ? 'text-muted' : change > 0 ? 'text-profit' : change < 0 ? 'text-loss' : 'text-muted';
  const live = leg?.live ?? rowLive ?? (leg?.data_source ?? '').toUpperCase() === 'WS';
  const expiry = leg?.expiry ?? rowExpiry;
  const strike = leg?.strike;
  const hasLtp = leg?.ltp != null && leg.ltp > 0;

  return (
    <div className={`options-ticker-cell options-ticker-${variant}`}>
      <div className="options-ticker-card__head">
        <span className="options-ticker-card__type">{variant.toUpperCase()}</span>
        <LiveDot live={live} />
      </div>
      <div className="options-ticker-card__ltp font-mono">
        {hasLtp ? formatPrice(leg!.ltp) : '—'}
      </div>
      <div className={`options-ticker-card__change font-mono text-xs ${changeClass}`}>
        {formatChange(change, changePct)}
      </div>
      <div className="options-ticker-card__meta text-2xs text-muted font-mono">
        {strike != null ? (
          <span className="options-ticker-card__strike">K {strike}</span>
        ) : (
          <span className="options-ticker-card__strike">ATM —</span>
        )}
        {expiry && <span className="options-ticker-card__expiry">{expiry}</span>}
      </div>
      {leg?.tradingsymbol && (
        <div className="options-ticker-card__symbol text-2xs text-muted font-mono truncate" title={leg.tradingsymbol}>
          {leg.tradingsymbol}
        </div>
      )}
    </div>
  );
}

function normalizeIndices(payload: OptionsDeskTickers | null): Record<string, OptionsDeskTickerRow> {
  const map: Record<string, OptionsDeskTickerRow> = {};
  if (!payload?.indices) return map;

  if (Array.isArray(payload.indices)) {
    for (const row of payload.indices) {
      const key = (row.underlying ?? '').toUpperCase();
      if (key) map[key] = row;
    }
    return map;
  }

  for (const [key, row] of Object.entries(payload.indices)) {
    map[key.toUpperCase()] = { ...row, underlying: row.underlying ?? key };
  }
  return map;
}

interface Props {
  marketOpen?: boolean;
}

export default function OptionsIndexTickerGrid({ marketOpen }: Props) {
  const [data, setData] = useState<OptionsDeskTickers | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.getOptionsDeskTickers()
      .then((r) => {
        setData(r);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Ticker feed unavailable';
        setLoadError(msg);
      });
  }, []);

  useEffect(() => {
    load();
    const pollMs = marketOpen === false ? 60000 : 12000;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [load, marketOpen]);

  const indexMap = useMemo(() => normalizeIndices(data), [data]);

  const rows = useMemo(
    () =>
      INDEX_ROWS.map((meta) => {
        const row = indexMap[meta.key];
        return {
          meta,
          row: row ?? ({ underlying: meta.key, label: meta.label } as OptionsDeskTickerRow),
        };
      }),
    [indexMap],
  );

  const liveCount = rows.filter(
    ({ row }) =>
      row.live
      || row.ce?.live
      || row.pe?.live
      || (row.ce?.data_source ?? '').toUpperCase() === 'WS'
      || (row.pe?.data_source ?? '').toUpperCase() === 'WS',
  ).length;

  const marketClosed = marketOpen === false;
  const feedLabel = marketClosed
    ? 'MARKET CLOSED'
    : loadError
      ? 'OFFLINE'
      : liveCount >= 3
        ? 'LIVE'
        : liveCount > 0
          ? 'PARTIAL'
          : data?.available
            ? 'POLLING'
            : 'WARMING';
  const feedClass =
    feedLabel === 'MARKET CLOSED'
      ? 'options-desk-pill options-desk-pill--closed'
      : feedLabel === 'LIVE'
        ? 'options-desk-pill options-desk-pill--live'
        : feedLabel === 'PARTIAL' || feedLabel === 'POLLING'
          ? 'options-desk-pill options-desk-pill--partial'
          : feedLabel === 'OFFLINE'
            ? 'options-desk-pill options-desk-pill--offline'
            : 'options-desk-pill';

  return (
    <section className="options-ticker-section" aria-label="Index option tickers">
      <div className="options-ticker-section__head">
        <div className="options-ticker-section__title">
          <Radio size={14} />
          <span>ATM option tickers</span>
        </div>
        <div className="options-ticker-section__meta">
          {data?.timestamp && (
            <span className="text-2xs text-muted font-mono">
              {new Date(data.timestamp).toLocaleTimeString('en-IN', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: true,
              })}
            </span>
          )}
          <span className={feedClass}>{feedLabel}</span>
        </div>
      </div>

      {loadError && (
        <p className="options-ticker-section__error text-xs text-muted m-0">
          Ticker feed unavailable — {loadError}
        </p>
      )}

      {marketClosed && !loadError && (
        <p className="options-ticker-section__hint text-xs text-muted m-0">
          REST polling may still show last-session spot and option quotes until the next open.
        </p>
      )}

      <div className="options-ticker-grid-wrap">
        <div className="options-ticker-grid" role="table">
          <div className="options-ticker-row options-ticker-row--head" role="row">
            <div className="options-ticker-cell options-ticker-cell--index" role="columnheader">Index</div>
            <div className="options-ticker-cell options-ticker-cell--spot" role="columnheader">Spot</div>
            <div className="options-ticker-cell options-ticker-cell--atm" role="columnheader">ATM</div>
            <div className="options-ticker-cell options-ticker-ce" role="columnheader">Call</div>
            <div className="options-ticker-cell options-ticker-pe" role="columnheader">Put</div>
          </div>

          {rows.map(({ meta, row }) => {
            const spotChangeClass =
              row.spot_change == null
                ? 'text-muted'
                : row.spot_change > 0
                  ? 'text-profit'
                  : row.spot_change < 0
                    ? 'text-loss'
                    : 'text-muted';

            return (
              <div key={meta.key} className="options-ticker-row" role="row">
                <div className={`options-ticker-cell options-ticker-cell--index ${meta.headerClass}`} role="cell">
                  <span className="options-ticker-index__label truncate">{row.label ?? meta.label}</span>
                  <LiveDot live={row.live} />
                </div>

                <div className="options-ticker-cell options-ticker-cell--spot" role="cell">
                  <span className="options-ticker-spot font-mono">
                    {row.spot != null ? formatPrice(row.spot) : '—'}
                  </span>
                  {row.spot_change != null && (
                    <span className={`options-ticker-spot__chg text-2xs font-mono ${spotChangeClass}`}>
                      {formatChange(row.spot_change, row.spot_change_pct)}
                    </span>
                  )}
                </div>

                <div className="options-ticker-cell options-ticker-cell--atm" role="cell">
                  <span className="options-ticker-atm font-mono">
                    {row.atm_strike != null ? row.atm_strike : '—'}
                  </span>
                  {row.expiry && (
                    <span className="options-ticker-atm__exp text-2xs text-muted font-mono">{row.expiry}</span>
                  )}
                </div>

                <TickerCard leg={row.ce} variant="ce" rowExpiry={row.expiry} rowLive={row.live} />
                <TickerCard leg={row.pe} variant="pe" rowExpiry={row.expiry} rowLive={row.live} />
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}