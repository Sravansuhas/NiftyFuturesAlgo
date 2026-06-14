import { BookOpen, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { MemoryInsights } from '../../api/types';
import { Panel, SectionTitle, Spinner } from './Shared';

export function LearningsTab() {
  const [insights, setInsights] = useState<MemoryInsights | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.getMemoryInsights();
      setInsights(data);
    } catch (e) {
      setInsights({ error: String(e) });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const notes = insights?.documentation_notes ?? [];

  return (
    <Panel>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <SectionTitle sub="Deterministic regime notes from backtest_memory — no LLM, data-volume aware">
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><BookOpen size={18} /> Market Learnings</span>
        </SectionTitle>
        <button type="button" className="btn btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Spinner /> Loading insights...</div>
      ) : insights?.error ? (
        <div className="bt-alert bt-alert-error">{insights.error}</div>
      ) : (
        <>
          {typeof insights?.total_runs === 'number' && (
            <div className="text-muted" style={{ fontSize: '0.8rem', marginBottom: 16 }}>
              Total runs in memory: <strong>{insights.total_runs}</strong>
            </div>
          )}

          {notes.length === 0 ? (
            <p className="text-muted" style={{ fontSize: '0.875rem' }}>
              Run several validations with different presets. High-confidence notes appear after enough trades and runs.
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {notes.map((note, i) => (
                <div key={i} className="bt-insight-card">{note}</div>
              ))}
            </div>
          )}

          {insights && !insights.error && (
            <details style={{ marginTop: 20 }}>
              <summary className="text-muted" style={{ fontSize: '0.75rem', cursor: 'pointer' }}>Raw insights JSON</summary>
              <pre className="bt-code-block">{JSON.stringify(insights, null, 2)}</pre>
            </details>
          )}
        </>
      )}
    </Panel>
  );
}