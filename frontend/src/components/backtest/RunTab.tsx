import { Play, Zap, StopCircle } from 'lucide-react';
import type { BacktestJob } from '../../api/types';
import type { BacktestFormState } from './presets';
import { BUILTIN_PRESETS } from './presets';
import { CheckboxRow, FieldLabel, Panel, ProgressBar, SectionTitle, Spinner, StatusBadge } from './Shared';

interface Props {
  form: BacktestFormState;
  setForm: React.Dispatch<React.SetStateAction<BacktestFormState>>;
  job: BacktestJob | null;
  isRunning: boolean;
  error: string | null;
  onRun: () => void;
  onQuickSynthetic: () => void;
  onCancel: () => void;
  onApplyPreset: (name: string) => void;
}

export function RunTab({ form, setForm, job, isRunning, error, onRun, onQuickSynthetic, onCancel, onApplyPreset }: Props) {
  const set = <K extends keyof BacktestFormState>(key: K, value: BacktestFormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const handleQuickToggle = (on: boolean) => {
    setForm((f) => ({
      ...f,
      quickMode: on,
      ...(on ? { folds: 1, researchMode: true, useRealData: false, entryOnNextBar: true } : {}),
    }));
  };

  return (
    <div className="bt-run-grid">
      <Panel>
        <SectionTitle sub="Walk-forward + regime validation with optional Kite historical data">
          Configure Validation
        </SectionTitle>

        <div className="bt-preset-row">
          {Object.keys(BUILTIN_PRESETS).map((name) => (
            <button key={name} type="button" className="btn btn-secondary" style={{ fontSize: '0.7rem', padding: '6px 10px' }} onClick={() => onApplyPreset(name)}>
              {name}
            </button>
          ))}
        </div>

        <div className="bt-form-grid" style={{ marginTop: 16 }}>
          <div>
            <FieldLabel hint="Months of historical data">Data window (months)</FieldLabel>
            <input className="input-field" type="number" min={1} max={12} value={form.months} onChange={(e) => set('months', Number(e.target.value))} />
          </div>
          <div>
            <FieldLabel hint="Rolling train/test splits">Walk-forward folds</FieldLabel>
            <input className="input-field" type="number" min={1} max={8} value={form.folds} disabled={form.quickMode} onChange={(e) => set('folds', Number(e.target.value))} />
          </div>
          <div>
            <FieldLabel>Risk range (low)</FieldLabel>
            <input className="input-field" type="number" step={0.0002} value={form.riskLow} onChange={(e) => set('riskLow', Number(e.target.value))} />
          </div>
          <div>
            <FieldLabel>Risk range (high)</FieldLabel>
            <input className="input-field" type="number" step={0.0002} value={form.riskHigh} onChange={(e) => set('riskHigh', Number(e.target.value))} />
          </div>
          <div>
            <FieldLabel>Max trades / day</FieldLabel>
            <select className="input-field" value={form.maxTrades} onChange={(e) => set('maxTrades', Number(e.target.value))}>
              {[2, 3, 4].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
          <div>
            <FieldLabel>Vol filter strictness</FieldLabel>
            <select className="input-field" value={form.volStrict} onChange={(e) => set('volStrict', Number(e.target.value))}>
              <option value={0.45}>Loose (0.45)</option>
              <option value={0.55}>Normal (0.55)</option>
              <option value={0.65}>Strict (0.65)</option>
            </select>
          </div>
        </div>

        <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <CheckboxRow checked={form.useRealData} onChange={(v) => set('useRealData', v)} label="Use real Kite historical data" accent="var(--intent-profit)" />
          <CheckboxRow checked={form.forceRefresh} onChange={(v) => set('forceRefresh', v)} label="Force refresh from Kite (ignore cache)" accent="var(--intent-loss)" />
          <CheckboxRow checked={form.researchMode} onChange={(v) => set('researchMode', v)} label="Research mode (relaxes filters)" />
          <CheckboxRow checked={form.entryOnNextBar} onChange={(v) => set('entryOnNextBar', v)} label="Entry on next bar (recommended)" />
          <CheckboxRow checked={form.quickMode} onChange={handleQuickToggle} label="Quick mode (1 fold, synthetic, fast iteration)" />
        </div>

        <div style={{ marginTop: 16 }}>
          <FieldLabel>Cost sensitivity</FieldLabel>
          <div style={{ display: 'flex', gap: 12, fontSize: '0.875rem' }}>
            {[1, 2, 3].map((m) => (
              <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <input type="radio" name="cost" checked={form.costMultiplier === m} onChange={() => set('costMultiplier', m)} />
                {m}×
              </label>
            ))}
          </div>
        </div>

        <button type="button" className="btn btn-primary" style={{ width: '100%', marginTop: 20, padding: '14px' }} disabled={isRunning} onClick={onRun}>
          <Play size={18} /> Run Full Validation
        </button>
        {isRunning && (
          <button type="button" className="btn btn-secondary" style={{ width: '100%', marginTop: 8, color: 'var(--intent-loss)', borderColor: 'var(--intent-loss)' }} onClick={onCancel}>
            <StopCircle size={16} /> Cancel Job
          </button>
        )}
        <button type="button" className="btn btn-secondary" style={{ width: '100%', marginTop: 8 }} disabled={isRunning} onClick={onQuickSynthetic}>
          <Zap size={16} /> Quick Synthetic (no Kite)
        </button>
      </Panel>

      <Panel style={{ display: 'flex', flexDirection: 'column', minHeight: 420 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <SectionTitle>Progress</SectionTitle>
          <StatusBadge status={isRunning ? 'running' : job?.status ?? 'READY'} />
        </div>

        <ProgressBar
          value={job?.progress ?? 0}
          label={job ? `${job.stage ?? 'running'} — ${job.progress ?? 0}%` : 'Idle — configure and run'}
          accent={isRunning ? 'var(--brand-primary)' : 'var(--intent-profit)'}
        />

        {error && (
          <div className="bt-alert bt-alert-error">{error}</div>
        )}
        {job?.error_code && (
          <div className="bt-alert bt-alert-error">Code: {job.error_code}</div>
        )}

        <div className="bt-log-area" style={{ flex: 1 }}>
          {isRunning ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 }}>
              <Spinner size={36} />
              <span className="text-muted" style={{ fontSize: '0.875rem' }}>Job running — {job?.stage}</span>
              {job?.gpu_available && <span className="text-muted" style={{ fontSize: '0.75rem' }}>GPU: {job.gpu_name}</span>}
            </div>
          ) : job?.status === 'completed' && job.result ? (
            <div style={{ fontSize: '0.8rem', lineHeight: 1.6 }}>
              <div className="text-profit" style={{ fontWeight: 600, marginBottom: 8 }}>Validation complete</div>
              <div>Avg return: {(job.result.avg_return ?? 0).toFixed(2)}%</div>
              <div>Profit factor: {(job.result.avg_pf ?? 0).toFixed(2)}</div>
              <div>Total trades: {job.result.total_trades ?? 0}</div>
              <div>Folds: {job.result.total_folds_run ?? 0}</div>
              {job.result.statistical_power?.message && (
                <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: 'var(--bg-base)', border: '1px solid var(--border-dim)' }}>
                  {job.result.statistical_power.message}
                </div>
              )}
            </div>
          ) : (
            <p className="text-muted" style={{ fontSize: '0.875rem' }}>
              Results and per-fold breakdown appear here after a successful run. Switch to Results or Learnings tabs for full detail.
            </p>
          )}
        </div>
      </Panel>
    </div>
  );
}