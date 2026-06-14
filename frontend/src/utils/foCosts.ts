import type { OptionsMtmSnapshot, PerSymbolStatus } from '../api/types';

/** Zerodha F&O — April 2026 statutory rates (see backtesting/costs.py, app/options_pnl.py). */
export const FUTURES_STT_SELL_RATE = 0.0005;
export const OPTIONS_STT_SELL_RATE = 0.0015;
export const BROKERAGE_PER_ORDER = 20;
export const FUTURES_OTHER_RT = 35;
export const FUTURES_OTHER_EXIT = 18;

export const INDEX_LOT_SIZES: Record<string, number> = {
  NIFTY: 65,
  BANKNIFTY: 30,
  SENSEX: 20,
};

export function futuresRoundTurnStatutory(price: number, lotSize: number): number {
  if (price <= 0 || lotSize <= 0) return BROKERAGE_PER_ORDER * 2 + FUTURES_OTHER_RT;
  const stt = price * lotSize * FUTURES_STT_SELL_RATE;
  return Math.round((BROKERAGE_PER_ORDER * 2 + stt + FUTURES_OTHER_RT) * 100) / 100;
}

export function futuresExitStatutory(price: number, lotSize: number): number {
  if (price <= 0 || lotSize <= 0) return BROKERAGE_PER_ORDER + FUTURES_OTHER_EXIT;
  const stt = price * lotSize * FUTURES_STT_SELL_RATE;
  return Math.round((BROKERAGE_PER_ORDER + stt + FUTURES_OTHER_EXIT) * 100) / 100;
}

export interface DailyPnlBreakdown {
  futuresGross: number;
  optionsGross: number;
  optionsNet: number;
  futuresStatutory: number;
  optionsStatutory: number;
  totalStatutory: number;
  combinedGross: number;
  combinedNet: number;
  futuresLotsTraded: number;
  openLegs: number;
}

export function computeDailyPnlBreakdown(
  futuresPnl: number,
  optionsMtm: OptionsMtmSnapshot | undefined,
  perSymbol: Record<string, PerSymbolStatus>,
  defaultLotSize = 65,
): DailyPnlBreakdown {
  const optionsGross = optionsMtm?.mtm_gross ?? optionsMtm?.mtm_net ?? 0;
  const optionsNet = optionsMtm?.mtm_net ?? 0;
  const optionsStatutory =
    optionsMtm?.mtm_gross != null && optionsMtm?.mtm_net != null
      ? Math.max(0, Math.round((optionsGross - optionsNet) * 100) / 100)
      : 0;

  let futuresStatutory = 0;
  let futuresLotsTraded = 0;
  let openLegs = 0;

  for (const [sym, pos] of Object.entries(perSymbol)) {
    const lotSize = INDEX_LOT_SIZES[sym] ?? defaultLotSize;
    const trades = pos.daily_trades ?? 0;
    const ltp = pos.avg_price && pos.avg_price > 0 ? pos.avg_price : 24_000;
    const mark = ltp;

    if (Math.abs(pos.position ?? 0) > 0) {
      openLegs += 1;
      futuresStatutory += futuresExitStatutory(mark, lotSize);
      const completed = Math.max(0, trades - 1);
      futuresLotsTraded += completed;
      futuresStatutory += completed * futuresRoundTurnStatutory(ltp, lotSize);
    } else if (trades > 0) {
      futuresLotsTraded += trades;
      futuresStatutory += trades * futuresRoundTurnStatutory(ltp, lotSize);
    }
  }

  if (futuresStatutory === 0 && futuresPnl !== 0) {
    const portfolioTrades = Object.values(perSymbol).reduce((n, p) => n + (p.daily_trades ?? 0), 0);
    const fallbackLots = Math.max(1, portfolioTrades);
    futuresLotsTraded = fallbackLots;
    futuresStatutory = fallbackLots * futuresRoundTurnStatutory(24_000, defaultLotSize);
  }

  futuresStatutory = Math.round(futuresStatutory * 100) / 100;
  const combinedGross = futuresPnl + optionsGross;
  const combinedNet = Math.round((futuresPnl - futuresStatutory + optionsNet) * 100) / 100;

  return {
    futuresGross: futuresPnl,
    optionsGross,
    optionsNet,
    futuresStatutory,
    optionsStatutory,
    totalStatutory: Math.round((futuresStatutory + optionsStatutory) * 100) / 100,
    combinedGross,
    combinedNet,
    futuresLotsTraded,
    openLegs,
  };
}