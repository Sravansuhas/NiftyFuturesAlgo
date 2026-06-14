import { Database, RefreshCw, Zap } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { CachedDataset, DataHealthReport } from '../../api/types';
import { useJobPoll } from '../../hooks/useJobPoll';
import type { BacktestFormState } from './presets';
import { deletePreset, loadSavedPresets, savePreset } from './presets';
import { CheckboxRow, Panel, ProgressBar, SectionTitle, Spinner, StatusBadge } from './Shared';

interface Props {
  form: BacktestFormState;
  setForm: React.Dispatch<React.SetStateAction<BacktestFormState>>;
}

export function DataTab({ form, setForm }: Props) {
  const [health, setHealth] = useState<DataHealthReport | null>(null);
  const [datasets, setDatasets] = useState<CachedDataset[]>([]);
  const [savedPresets, setSavedPresets] = useState<Record<string, BacktestFormState>>({});
  const [presetName, setPresetName] = useState('');
  const [healthLoading, setHealthLoading] = useState(true);
  const dataPoll = useJobPoll(1200);

  const refreshHealth = useCallback(async () => {
    setHealthLoading(true);
    try {
      const [h, d] = await Promise.all([api.getDataHealth(), api.getCachedDatasets()]);
      setHealth(h);
      setDatasets(d.datasets ?? []);
    } catch (e) {
      setHealth({ overall: 'error', error: String(e) });
    } finally {
      setHealthLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    setSavedPresets(loadSavedPresets());
  }, [refreshHealth]);

  useEffect(() => {
    if (dataPoll.job?.status === 'completed') refreshHealth();
  }, [dataPoll.job?.status, refreshHealth]);

  const startFetch = async (opts: { force?: boolean; localOnly?: boolean }) => {
    const { job_id } = await api.fetchHistoricalData({
      months: form.months,
      force_refresh: opts.force,
      use_local_only: opts.localOnly,
    });
    dataPoll.start(job_id);
  };

  const progressLabel = () => {
    const j = dataPoll.job;
    if (!j) return 'Idle';
    if (j.contract) return `Fetching ${j.contract} (${j.contract_index}/${j.contract_total}) — ${j.progress}%`;
    if (j.stage === 'scanning_local_cache') return `Scanning cache — ${j.progress}%`;
    if (j.stage === 'saving_cache') return `Saving parquet — ${j.progress}%`;
    if (j.status === 'completed') return `Done — ${j.rows?.toLocaleString() ?? '?'} rows`;
    return `${j.stage ?? 'working'} — ${j.progress ?? 0}%`;
  };

  const saveCurrentPreset = () => {
    const name = presetName.trim() || `preset_${Date.now()}`;
    savePreset(name, form);
    setSavedPresets(loadSavedPresets());
    setPresetName('');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Panel>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <SectionTitle sub="Parquet cache under data/historical_cache — Docker Postgres is optional, not used for OHLCV">
            <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Database size={18} /> Local Data Health</span>
          </SectionTitle>
          <button type="button" className="btn btn-secondary" style={{ fontSize: '0.75rem' }} onClick={refreshHealth} disabled={healthLoading}>
            <RefreshCw size={14} /> Re-check
          </button>
        </div>

        {healthLoading ? (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><Spinner /> Scanning...</div>
        ) : health && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <StatusBadge status={health.overall} />
              {health.latest_data_date && (
                <span className="text-muted" style={{ fontSize: '0.75rem' }}>Latest: {health.latest_data_date}</span>
              )}
            </div>
            <p className="text-muted" style={{ fontSize: '0.8rem', marginBottom: 12 }}>{health.recommendation}</p>

            <div className="bt-file-list">
              {(health.parquet_files ?? []).map((f) => (
                <div key={f.path ?? f.filename} className="bt-file-row">
                  <span className={f.status === 'ok' ? 'text-profit' : f.status === 'stale' ? 'text-brand' : 'text-loss'}>
                    {f.status === 'ok' ? '✓' : f.status === 'stale' ? '⏳' : '✗'} {f.filename}
                  </span>
                  <span className="text-muted font-mono" style={{ fontSize: '0.7rem' }}>
                    {f.actual_from}→{f.actual_to} · {f.rows ?? '?'} rows
                  </span>
                </div>
              ))}
              {(health.parquet_files ?? []).length === 0 && (
                <p className="text-muted" style={{ fontSize: '0.8rem' }}>No parquet files found.</p>
              )}
            </div>

            {health.kite_api && (
              <div className="text-muted" style={{ fontSize: '0.7rem', marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-dim)' }}>
                Kite intervals: {(health.kite_api.intervals ?? []).join(', ')} · default: {health.kite_api.project_default_interval}
                {health.kite_api.docs_url && (
                  <> · <a href={health.kite_api.docs_url} target="_blank" rel="noreferrer" className="text-brand">docs</a></>
                )}
              </div>
            )}
          </>
        )}
      </Panel>

      <div className="bt-run-grid">
        <Panel>
          <SectionTitle>Download Historical Data</SectionTitle>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
            <button type="button" className="btn btn-secondary" disabled={dataPoll.isRunning} onClick={() => startFetch({})}>
              Smart Download
            </button>
            <button type="button" className="btn btn-secondary" disabled={dataPoll.isRunning} onClick={() => startFetch({ localOnly: true })}>
              <Zap size={14} /> Load Local Cache
            </button>
            <button type="button" className="btn btn-secondary" style={{ color: 'var(--intent-loss)', borderColor: 'rgba(239,68,68,0.4)' }} disabled={dataPoll.isRunning} onClick={() => startFetch({ force: true })}>
              Force Refresh (Kite)
            </button>
          </div>

          {(dataPoll.isRunning || (dataPoll.job && dataPoll.job.progress > 0)) && (
            <ProgressBar value={dataPoll.job?.progress ?? 0} label={progressLabel()} accent="var(--intent-profit)" />
          )}
          {dataPoll.job?.status === 'failed' && (
            <div className="bt-alert bt-alert-error">{dataPoll.job.error}</div>
          )}

          <CheckboxRow
            checked={form.useRealData}
            onChange={(v) => setForm((f) => ({ ...f, useRealData: v }))}
            label="Backtests will use real data when checked (Run tab)"
          />
        </Panel>

        <Panel>
          <SectionTitle sub={`${datasets.length} cached dataset(s)`}>Cached Datasets</SectionTitle>
          <div className="bt-file-list" style={{ maxHeight: 200 }}>
            {datasets.map((d) => (
              <div key={d.filename} className="bt-file-row">
                <span className="font-mono text-profit" style={{ fontSize: '0.75rem', maxWidth: '55%', overflow: 'hidden', textOverflow: 'ellipsis' }} title={d.filename}>
                  {d.filename}
                </span>
                <span className="text-muted" style={{ fontSize: '0.7rem', textAlign: 'right' }}>
                  {(d.actual_from ?? d.file_from)}→{(d.actual_to ?? d.file_to)}
                  <br />
                  {typeof d.rows === 'number' ? d.rows.toLocaleString() : d.rows} rows · {d.size_kb}KB
                </span>
              </div>
            ))}
            {datasets.length === 0 && <p className="text-muted" style={{ fontSize: '0.8rem' }}>No datasets yet — run a download.</p>}
          </div>
        </Panel>
      </div>

      <Panel>
        <SectionTitle>Saved Presets (localStorage)</SectionTitle>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {Object.keys(savedPresets).map((name) => (
            <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <button type="button" className="btn btn-secondary" style={{ fontSize: '0.75rem' }} onClick={() => setForm(savedPresets[name])}>
                {name}
              </button>
              <button type="button" className="btn btn-secondary" style={{ fontSize: '0.7rem', padding: '4px 8px' }} onClick={() => { deletePreset(name); setSavedPresets(loadSavedPresets()); }}>
                ×
              </button>
            </div>
          ))}
          {Object.keys(savedPresets).length === 0 && <span className="text-muted" style={{ fontSize: '0.8rem' }}>No saved presets</span>}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="input-field" placeholder="Preset name" value={presetName} onChange={(e) => setPresetName(e.target.value)} style={{ maxWidth: 200 }} />
          <button type="button" className="btn btn-secondary" onClick={saveCurrentPreset}>Save Current Form</button>
        </div>
      </Panel>
    </div>
  );
}