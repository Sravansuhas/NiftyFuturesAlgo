export interface BacktestFormState {
  months: number;
  folds: number;
  riskLow: number;
  riskHigh: number;
  maxTrades: number;
  volStrict: number;
  useRealData: boolean;
  forceRefresh: boolean;
  researchMode: boolean;
  entryOnNextBar: boolean;
  quickMode: boolean;
  costMultiplier: number;
}

export const DEFAULT_FORM: BacktestFormState = {
  months: 5,
  folds: 5,
  riskLow: 0.0032,
  riskHigh: 0.0038,
  maxTrades: 3,
  volStrict: 0.55,
  useRealData: true,
  forceRefresh: false,
  researchMode: false,
  entryOnNextBar: true,
  quickMode: false,
  costMultiplier: 1.0,
};

export const BUILTIN_PRESETS: Record<string, BacktestFormState> = {
  conservative: {
    ...DEFAULT_FORM,
    months: 6,
    folds: 5,
    riskLow: 0.0028,
    riskHigh: 0.0032,
    maxTrades: 2,
    volStrict: 0.6,
    researchMode: false,
    costMultiplier: 1.0,
  },
  balanced: { ...DEFAULT_FORM },
  aggressive: {
    ...DEFAULT_FORM,
    months: 4,
    folds: 4,
    riskLow: 0.0038,
    riskHigh: 0.0045,
    maxTrades: 4,
    volStrict: 0.45,
    researchMode: true,
    costMultiplier: 1.0,
  },
  live: {
    ...DEFAULT_FORM,
    months: 5,
    folds: 5,
    riskLow: 0.0032,
    riskHigh: 0.0038,
    maxTrades: 3,
    volStrict: 0.55,
    useRealData: true,
    entryOnNextBar: true,
    quickMode: false,
  },
  quick: {
    ...DEFAULT_FORM,
    months: 3,
    folds: 1,
    quickMode: true,
    researchMode: true,
    useRealData: false,
    entryOnNextBar: true,
  },
};

const STORAGE_KEY = 'nfa_backtest_presets';

export function loadSavedPresets(): Record<string, BacktestFormState> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function savePreset(name: string, state: BacktestFormState) {
  const all = loadSavedPresets();
  all[name] = state;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
}

export function deletePreset(name: string) {
  const all = loadSavedPresets();
  delete all[name];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
}