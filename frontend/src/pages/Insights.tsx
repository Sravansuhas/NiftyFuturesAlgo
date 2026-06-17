import { Brain, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import EmptyState from '../components/ui/EmptyState';
import PageShell from '../components/ui/PageShell';
import type { AgentInsights } from '../api/types';

const INDICES = ['NIFTY', 'BANKNIFTY', 'SENSEX'] as const;

function TileHeader({ title, badge }: { title: string; badge?: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <h3 className="tile-eyebrow m-0">{title}</h3>
      {badge ? <span className="badge badge-muted">{badge}</span> : null}
    </div>
  );
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`text-xs font-bold uppercase px-2 py-1 rounded-full ${
        ok ? 'text-profit bg-intent-profit-dim' : 'text-loss bg-intent-loss-dim'
      }`}
    >
      {label}
    </span>
  );
}

export default function Insights() {
  const [insights, setInsights] = useState<AgentInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const data = await api.getAgentInsights(refresh);
      if (data.error) {
        setError(data.error);
        setInsights(null);
      } else {
        setInsights(data);
      }
    } catch (e) {
      setError(String(e));
      setInsights(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  const refreshAction = (
    <button
      type="button"
      className="btn btn-secondary flex items-center gap-2"
      disabled={refreshing}
      onClick={() => load(true)}
    >
      <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
      Refresh
    </button>
  );

  if (loading && !insights) {
    return (
      <PageShell subtitle="Loading agent insights…" actions={refreshAction}>
        <div className="bento-grid insights-grid w-full">
          <div className="bento-tile journal-col-12">
            <EmptyState
              icon={Brain}
              title="Loading"
              message="Aggregating promotion, WFO, and proposals."
            />
          </div>
        </div>
      </PageShell>
    );
  }

  if (error && !insights) {
    return (
      <PageShell subtitle="Agent insights unavailable" actions={refreshAction}>
        <div className="bento-grid insights-grid w-full">
          <div className="bento-tile journal-col-12">
            <EmptyState icon={Brain} title="Error" message={error} />
          </div>
        </div>
      </PageShell>
    );
  }

  const promo = insights?.promotion_status ?? {};
  const wfo = insights?.multi_index_wfo;
  const pending = insights?.pending_proposals;
  const lunar = insights?.lunar_context;
  const market = insights?.market_context;

  return (
    <PageShell
      subtitle="Promotion gates, multi-index WFO, human-gated proposals, and research context."
      actions={refreshAction}
    >
      <div className="bento-grid insights-grid w-full">
        <div className="bento-tile bento-tile--auto">
          <TileHeader title="Promotion Status" badge={insights?.date_ist} />
          <div className="flex flex-col">
            {INDICES.map((idx) => {
              const row = promo[idx] ?? {};
              return (
                <div key={idx} className="insight-row">
                  <span className="font-mono font-semibold">{idx}</span>
                  <div className="flex items-center gap-2 text-xs text-muted">
                    <span>{row.status ?? 'no_record'}</span>
                    <StatusPill ok={Boolean(row.passed)} label={row.passed ? 'PASS' : 'FAIL'} />
                  </div>
                </div>
              );
            })}
            {insights?.promotion_summary ? (
              <p className="text-xs text-muted m-0 mt-1">
                Any passed: {insights.promotion_summary.any_passed ? 'yes' : 'no'} · All passed:{' '}
                {insights.promotion_summary.all_passed ? 'yes' : 'no'}
              </p>
            ) : null}
          </div>
        </div>

        <div className="bento-tile bento-tile--auto">
          <TileHeader
            title="Multi-Index WFO"
            badge={wfo?.has_report ? wfo.run_id ?? 'report' : 'none'}
          />
          {wfo?.has_report ? (
            <div className="text-sm flex flex-col gap-2">
              <p className="m-0 text-muted">
                Finished: <span className="text-main font-mono">{wfo.finished_at ?? '—'}</span>
              </p>
              {wfo.summary ? (
                <p className="m-0">
                  Passed{' '}
                  <strong>
                    {wfo.summary.passed_count ?? 0}/{wfo.summary.index_count ?? 3}
                  </strong>{' '}
                  indices
                </p>
              ) : null}
              <div className="page-kv-list mt-1">
                {INDICES.map((idx) => {
                  const row = wfo.per_index?.[idx];
                  if (!row?.has_record) return null;
                  return (
                    <div key={idx} className="data-row">
                      <span className="text-xs font-mono">{idx}</span>
                      <span className={`text-xs font-mono ${row.passed ? 'text-profit' : 'text-loss'}`}>
                        {row.passed ? 'PASS' : 'FAIL'} PF={row.avg_pf ?? '—'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted m-0">
              No multi_index report in data/wfo_runs/. Run{' '}
              <code className="text-xs">algo_lab_ops.py wfo-run</code>.
            </p>
          )}
        </div>

        <div className="bento-tile bento-tile--auto">
          <TileHeader title="Pending Proposals" badge={`${pending?.count ?? 0}`} />
          {(pending?.proposals?.length ?? 0) > 0 ? (
            <ul className="m-0 p-0 list-none flex flex-col gap-2 text-sm">
              {pending!.proposals!.slice(0, 6).map((p) => (
                <li key={p.id ?? p.proposal_id} className="border-b border-dim pb-2 last:border-0">
                  <div className="font-mono text-xs text-brand">{p.id ?? p.proposal_id}</div>
                  <div className="text-muted text-xs mt-1 line-clamp-2">{p.description}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted m-0">No proposals awaiting founder review.</p>
          )}
          <p className="text-xs text-warn m-0 mt-2">Human gate required — never auto-applied.</p>
        </div>

        <div className="bento-tile bento-tile--auto">
          <TileHeader title="Lunar Context" badge="research only" />
          {lunar?.available ? (
            <div className="text-sm flex flex-col gap-1">
              <p className="m-0">
                <span className="text-muted">Phase:</span> {lunar.phase_name}
              </p>
              <p className="m-0">
                <span className="text-muted">Tithi:</span> {lunar.tithi_name} ({lunar.paksha})
              </p>
              <p className="m-0 text-xs text-muted">
                Illumination {lunar.illumination_pct ?? '—'}% · tag {lunar.folklore_tag ?? '—'}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted m-0">Run algo_lab_ops lunar to build context.</p>
          )}
        </div>

        <div className="bento-tile bento-tile--auto">
          <TileHeader title="Market Context" />
          {market?.available ? (
            <p className="text-sm m-0 text-profit">Loaded from {market.path}</p>
          ) : (
            <p className="text-sm text-muted m-0">
              Optional data/market_context.json not present.
            </p>
          )}
        </div>

        <div className="bento-tile bento-tile--auto">
          <TileHeader title="Founder Actions" />
          <ul className="m-0 pl-4 text-sm flex flex-col gap-2">
            {(insights?.founder_actions ?? []).map((action) => (
              <li key={action} className="text-muted">
                {action}
              </li>
            ))}
          </ul>
        </div>

        {(insights?.documentation_notes?.length ?? 0) > 0 ? (
          <div className="bento-tile bento-tile--auto journal-col-12 insights-col-full">
            <h3 className="tile-eyebrow">Documentation Notes</h3>
            <ul className="m-0 pl-4 text-sm text-muted flex flex-col gap-2">
              {insights!.documentation_notes!.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </PageShell>
  );
}