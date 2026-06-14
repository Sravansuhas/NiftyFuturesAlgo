import { BarChart2, Cpu, Download, ShieldAlert } from 'lucide-react';
import type { BacktestResult } from '../../api/types';
import { KpiGrid, Panel, SectionTitle } from './Shared';

function mcSummary(mc: Record<string, unknown> | undefined) {
  if (!mc || mc.message) return null;
  const median = mc.median_final_equity ?? mc.median_equity;
  const p5 = mc.p5_final_equity ?? mc.p5_equity;
  const p95 = mc.p95_final_equity ?? mc.p95_equity;
  if (median == null) return null;
  return { median, p5, p95, n: mc.n_trades ?? mc.trades_used };
}

export function ResultsTab({ result }: { result: BacktestResult | null | undefined }) {
  if (!result) {
    return (
      <Panel>
        <p className="text-muted">No results yet. Run a validation from the Run tab.</p>
      </Panel>
    );
  }

  const kpis = [
    { label: 'Avg Return', value: `${(result.avg_return ?? 0).toFixed(2)}%`, color: (result.avg_return ?? 0) >= 0 ? 'var(--intent-profit)' : 'var(--intent-loss)' },
    { label: 'Profit Factor', value: (result.avg_pf ?? 0).toFixed(2) },
    { label: 'Total Trades', value: String(result.total_trades ?? 0) },
    { label: 'Folds', value: String(result.total_folds_run ?? 0) },
  ];

  const folds = result.folds ?? [];
  const chartPoints = folds.map((f, i, arr) => {
    const x = arr.length > 1 ? (i / (arr.length - 1)) * 800 : 400;
    const y = 280 - Math.min(240, Math.max(-40, f.test_return * 8));
    return `${i === 0 ? 'M' : 'L'} ${x} ${y}`;
  }).join(' ');

  const topMc = mcSummary(result.monte_carlo);
  const costSens = result.cost_sensitivity_summary;
  const gpuLabel = result.gpu_used
    ? `GPU: ${result.gpu_device ?? result.gpu_name ?? 'active'}`
    : result.gpu_available
      ? `GPU available (${result.gpu_name ?? 'CUDA'}) — CPU MC used`
      : 'CPU Monte Carlo';

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backtest_result_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Panel>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <SectionTitle sub="Walk-forward fold performance and statistical power">
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><BarChart2 size={18} /> Detailed Results</span>
        </SectionTitle>
        <button type="button" className="btn btn-secondary" onClick={exportJson}>
          <Download size={14} /> Export JSON
        </button>
      </div>

      {result.data_warning && (
        <div className="bt-alert bt-alert-warn" style={{ marginBottom: 12 }}>
          <ShieldAlert size={14} style={{ display: 'inline', marginRight: 6 }} />
          {result.data_warning}
          {result.data_source && (
            <div style={{ marginTop: 4, fontSize: '0.75rem', opacity: 0.85 }}>
              Source: {result.data_source} · {result.data_bars ?? '?'} bars
            </div>
          )}
        </div>
      )}

      {result.statistical_power && (
        <div className={`bt-alert ${result.statistical_power.warning_level === 'ok' ? 'bt-alert-ok' : 'bt-alert-warn'}`} style={{ marginBottom: 16 }}>
          {result.statistical_power.message}
          {result.statistical_power.recommendation && (
            <div style={{ marginTop: 6, fontSize: '0.75rem', opacity: 0.9 }}>{result.statistical_power.recommendation}</div>
          )}
        </div>
      )}

      <KpiGrid items={kpis} />

      <div className="bt-kpi-grid" style={{ marginTop: 12, marginBottom: 16 }}>
        <div className="bt-kpi-card" style={{ padding: '12px 14px' }}>
          <div className="text-muted" style={{ fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: 6 }}>
            <Cpu size={12} /> {gpuLabel}
          </div>
          {topMc && (
            <div style={{ fontSize: '0.8rem', marginTop: 6 }}>
              MC median equity: ₹{Number(topMc.median).toLocaleString('en-IN')}
              {topMc.p5 != null && topMc.p95 != null && (
                <span className="text-muted"> · P5–P95 ₹{Number(topMc.p5).toLocaleString('en-IN')} – ₹{Number(topMc.p95).toLocaleString('en-IN')}</span>
              )}
            </div>
          )}
        </div>
        {costSens && !costSens.note && (
          <div className="bt-kpi-card" style={{ padding: '12px 14px' }}>
            <div className="text-muted" style={{ fontSize: '0.7rem' }}>Cost sensitivity (net PnL)</div>
            <div style={{ fontSize: '0.8rem', marginTop: 6, display: 'flex', gap: 12 }}>
              {Object.entries(costSens).map(([k, v]) => (
                <span key={k}>{k}: <strong>₹{Number(v).toLocaleString('en-IN')}</strong></span>
              ))}
            </div>
          </div>
        )}
        {costSens?.note && (
          <div className="bt-kpi-card" style={{ padding: '12px 14px' }}>
            <div className="text-muted" style={{ fontSize: '0.7rem' }}>Cost sensitivity</div>
            <div style={{ fontSize: '0.75rem', marginTop: 6 }}>{costSens.note}</div>
          </div>
        )}
      </div>

      <div className="bt-chart-wrap">
        {folds.length > 0 ? (
          <svg viewBox="0 0 800 300" preserveAspectRatio="none" style={{ width: '100%', height: '100%' }}>
            {[50, 100, 150, 200, 250].map((y) => (
              <line key={y} x1="0" y1={y} x2="800" y2={y} stroke="var(--border-dim)" strokeWidth="1" strokeDasharray="4 4" />
            ))}
            <path d={`${chartPoints} L 800 300 L 0 300 Z`} fill="rgba(99, 102, 241, 0.15)" />
            <path d={chartPoints} fill="none" stroke="var(--brand-primary)" strokeWidth="3" />
          </svg>
        ) : (
          <p className="text-muted">No fold data returned</p>
        )}
      </div>

      {folds.length > 0 && (
        <div className="bt-fold-table">
          <div className="bt-fold-head">
            <span>Fold</span><span>Return</span><span>PF</span><span>DD</span><span>Trades</span>
          </div>
          {folds.map((f) => (
            <div key={f.fold} className="bt-fold-row">
              <span>F{f.fold}</span>
              <span className={f.test_return >= 0 ? 'text-profit' : 'text-loss'}>{f.test_return.toFixed(2)}%</span>
              <span>{f.test_pf.toFixed(2)}</span>
              <span className="text-loss">{f.test_dd.toFixed(2)}%</span>
              <span>{f.trades}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}