import { Receipt } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../api/client';
import type { RealFillsAnalysis } from '../../api/types';
import { Panel, SectionTitle, Spinner } from './Shared';

export function FillsTab() {
  const [data, setData] = useState<RealFillsAnalysis | null>(null);
  const [loading, setLoading] = useState(false);

  const analyze = async () => {
    setLoading(true);
    try {
      setData(await api.getRealFillsAnalysis(40));
    } catch (e) {
      setData({ error: String(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Panel>
      <SectionTitle sub="Kite /trades + cost model calibration for learning layer">
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Receipt size={18} /> Real Fills & Costs</span>
      </SectionTitle>

      <button type="button" className="btn btn-primary" onClick={analyze} disabled={loading}>
        {loading ? <Spinner size={16} /> : null}
        Pull Last Fills from Kite
      </button>

      {data?.error && <div className="bt-alert bt-alert-error" style={{ marginTop: 16 }}>{data.error}</div>}

      {data && !data.error && (
        <div style={{ marginTop: 20 }}>
          <div className="bt-kpi-grid" style={{ marginBottom: 16 }}>
            <div className="bt-kpi">
              <div className="text-muted" style={{ fontSize: '0.7rem' }}>FILLS ANALYZED</div>
              <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 700 }}>{data.summary?.nifty_fills_analyzed ?? 0}</div>
            </div>
            <div className="bt-kpi">
              <div className="text-muted" style={{ fontSize: '0.7rem' }}>EST. COSTS</div>
              <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 700 }}>₹{data.summary?.est_total_cost_rs ?? 0}</div>
            </div>
            <div className="bt-kpi">
              <div className="text-muted" style={{ fontSize: '0.7rem' }}>ORDERS SEEN</div>
              <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 700 }}>{data.recent_orders_count ?? '—'}</div>
            </div>
          </div>

          {(data.documentation_notes ?? []).map((n, i) => (
            <div key={i} className="bt-insight-card" style={{ borderColor: 'rgba(245, 158, 11, 0.3)' }}>{n}</div>
          ))}

          {(data.fills ?? []).length > 0 && (
            <div className="bt-code-block" style={{ marginTop: 12, maxHeight: 220 }}>
              {(data.fills ?? []).slice(0, 12).map((f, i) => (
                <div key={i}>
                  {f.ts?.slice(0, 16)} {f.symbol} {f.qty} @ {f.price} → ₹{f.est_cost_round_turn_rs}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}