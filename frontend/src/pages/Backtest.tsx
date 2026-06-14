import { FlaskConical } from 'lucide-react';
import { useState } from 'react';
import { api } from '../api/client';
import { DataTab } from '../components/backtest/DataTab';
import { FillsTab } from '../components/backtest/FillsTab';
import { LearningsTab } from '../components/backtest/LearningsTab';
import { BUILTIN_PRESETS, DEFAULT_FORM, type BacktestFormState } from '../components/backtest/presets';
import { ResultsTab } from '../components/backtest/ResultsTab';
import { RunTab } from '../components/backtest/RunTab';
import { TabBar, type BacktestTab } from '../components/backtest/Shared';
import { useJobPoll } from '../hooks/useJobPoll';

export default function Backtest() {
  const [tab, setTab] = useState<BacktestTab>('run');
  const [form, setForm] = useState<BacktestFormState>(DEFAULT_FORM);
  const [error, setError] = useState<string | null>(null);
  const backtestPoll = useJobPoll(1500);

  const applyPreset = (name: string) => {
    const preset = BUILTIN_PRESETS[name];
    if (preset) setForm({ ...preset });
  };

  const runBacktest = async (quickSynthetic = false) => {
    setError(null);
    backtestPoll.reset();

    const payload = quickSynthetic
      ? { ...form, quickMode: true, useRealData: false, folds: 1, researchMode: true, entryOnNextBar: true }
      : form;

    try {
      const { job_id } = await api.runBacktest({
        months: payload.months,
        folds: payload.folds,
        risk_low: payload.riskLow,
        risk_high: payload.riskHigh,
        max_trades: payload.maxTrades,
        vol_strict: payload.volStrict,
        research_mode: payload.researchMode,
        cost_multiplier: payload.costMultiplier,
        entry_on_next_bar: payload.entryOnNextBar,
        quick_mode: payload.quickMode || quickSynthetic,
        use_real_data: payload.useRealData,
        force_refresh: payload.forceRefresh,
      });
      backtestPoll.start(job_id);
    } catch (e) {
      setError(String(e));
    }
  };

  const onRunComplete = backtestPoll.job?.status === 'completed';

  return (
    <div className="bt-page">
      <header className="bt-header">
        <div>
          <p className="page-subtitle m-0 flex items-center gap-2">
            <FlaskConical size={16} className="text-brand flex-shrink-0" />
            Walk-forward validation · regime learnings · Kite historical data
          </p>
        </div>
        {onRunComplete && (
          <button type="button" className="btn btn-secondary" onClick={() => setTab('results')}>
            View Results →
          </button>
        )}
      </header>

      <TabBar active={tab} onChange={setTab} />

      <div className="bt-tab-body">
        {tab === 'run' && (
          <RunTab
            form={form}
            setForm={setForm}
            job={backtestPoll.job}
            isRunning={backtestPoll.isRunning}
            error={error ?? backtestPoll.job?.error ?? null}
            onRun={() => runBacktest(false)}
            onQuickSynthetic={() => runBacktest(true)}
            onCancel={backtestPoll.cancel}
            onApplyPreset={applyPreset}
          />
        )}
        {tab === 'results' && <ResultsTab result={backtestPoll.job?.result} />}
        {tab === 'learnings' && <LearningsTab />}
        {tab === 'fills' && <FillsTab />}
        {tab === 'data' && <DataTab form={form} setForm={setForm} />}
      </div>
    </div>
  );
}