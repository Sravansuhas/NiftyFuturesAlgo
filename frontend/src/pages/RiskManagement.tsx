import { AlertTriangle, Lock, ShieldAlert } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import EmptyState from '../components/ui/EmptyState';
import PageShell from '../components/ui/PageShell';
import type { RiskConfig } from '../api/types';
import { formatINR } from '../utils/format';
import { computeDailyPnlBreakdown } from '../utils/foCosts';

export default function RiskManagement() {
  const [risk, setRisk] = useState<RiskConfig | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getRiskConfig()
      .then(setRisk)
      .catch(() => setRisk(null))
      .finally(() => setLoading(false));
    const id = setInterval(() => {
      api.getRiskConfig().then(setRisk).catch(() => {});
    }, 10000);
    return () => clearInterval(id);
  }, []);

  if (loading) {
    return (
      <PageShell>
        <EmptyState variant="centered" title="Loading risk configuration…" />
      </PageShell>
    );
  }

  if (!risk?.loaded) {
    return (
      <PageShell>
        <EmptyState
          variant="centered"
          icon={ShieldAlert}
          title="Risk engine unavailable"
          message="Start the backend with python run.py --dev to load live limits from RiskGatekeeper."
        />
      </PageShell>
    );
  }

  const dailyLossUsed = Math.abs(risk.daily_loss);
  const dailyLossLimit = risk.max_daily_loss_rs;
  const lossPctUsed = dailyLossLimit > 0 ? (dailyLossUsed / dailyLossLimit) * 100 : 0;
  const pnlBreakdown = computeDailyPnlBreakdown(risk.daily_pnl, undefined, {}, risk.lot_size);

  return (
    <PageShell
      subtitle={<>Live limits from RiskGatekeeper — state <span className="font-mono text-main">{risk.state}</span></>}
    >
      <div className="bento-grid">
        <div className="bento-tile" style={{ gridColumn: 'span 12' }}>
          <h3 className="tile-title">
            <Lock size={18} /> Capital Protection (Live)
          </h3>

          <div className="grid grid-cols-2 gap-6">
            <div className="metric-stack">
              <span className="stat-strip-label">Max Daily Loss (MTM)</span>
              <span className="font-mono text-xl font-bold text-loss">{formatINR(-dailyLossLimit)}</span>
              <div className="risk-meter">
                <div
                  className="risk-meter-fill"
                  style={{
                    width: `${Math.min(100, lossPctUsed)}%`,
                    backgroundColor: lossPctUsed > 80 ? 'var(--intent-loss)' : 'var(--brand-primary)',
                  }}
                />
              </div>
              <span className="text-xs text-muted">
                Used: {formatINR(-dailyLossUsed)} ({lossPctUsed.toFixed(0)}% of limit)
              </span>
            </div>

            <div className="metric-stack">
              <span className="stat-strip-label">Max Drawdown (Trailing)</span>
              <span className="font-mono text-xl font-bold">
                {(risk.max_drawdown_pct * 100).toFixed(1)}% ({formatINR(risk.max_drawdown_rs)})
              </span>
              <span className="text-xs text-muted">
                Current drawdown:{' '}
                <span className={risk.current_drawdown_pct > risk.max_drawdown_pct * 100 * 0.8 ? 'text-loss' : 'text-main'}>
                  {risk.current_drawdown_pct.toFixed(2)}%
                </span>
              </span>
            </div>
          </div>
        </div>

        <div className="bento-tile" style={{ gridColumn: 'span 12' }}>
          <h3 className="tile-title">Position Sizing</h3>

          <div className="stat-strip border-b-0 mb-0 pb-0">
            <div className="stat-strip-item">
              <span className="stat-strip-label">Risk Per Trade</span>
              <span className="stat-strip-value">{(risk.risk_per_trade_pct * 100).toFixed(2)}%</span>
              <span className="text-xs text-muted">≈ {formatINR(risk.capital * risk.risk_per_trade_pct)} per trade</span>
            </div>
            <div className="stat-strip-item">
              <span className="stat-strip-label">Max Order Quantity</span>
              <span className="stat-strip-value">{risk.max_order_quantity} qty</span>
              <span className="text-xs text-muted">Lot {risk.lot_size} · Max lots {risk.max_lots}</span>
            </div>
            <div className="stat-strip-item">
              <span className="stat-strip-label">Max Trades / Day</span>
              <span className="stat-strip-value">{risk.trades_today} / {risk.max_trades_per_day}</span>
            </div>
            <div className="stat-strip-item">
              <span className="stat-strip-label">Futures P&L (gross)</span>
              <span className={`stat-strip-value ${risk.daily_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {formatINR(risk.daily_pnl, true)}
              </span>
            </div>
            <div className="stat-strip-item">
              <span className="stat-strip-label">Est. net after tax</span>
              <span className={`stat-strip-value ${pnlBreakdown.combinedNet >= 0 ? 'text-profit' : 'text-loss'}`}>
                {formatINR(pnlBreakdown.combinedNet, true)}
              </span>
            </div>
          </div>
        </div>

        <div
          className="bento-tile"
          style={{
            gridColumn: 'span 12',
            borderColor: risk.force_dry_run ? 'var(--brand-primary)' : 'var(--intent-loss)',
          }}
        >
          <h3 className="tile-title">
            <AlertTriangle size={18} className={risk.force_dry_run ? 'text-brand' : 'text-loss'} /> Safety Mode
          </h3>
          <p className="text-sm text-muted m-0 leading-relaxed">
            {risk.force_dry_run
              ? 'FORCE_DRY_RUN is active — all orders are simulated. No real capital at risk.'
              : 'LIVE MODE — real orders will be placed. Ensure all limits are correct.'}
          </p>
        </div>
      </div>
    </PageShell>
  );
}