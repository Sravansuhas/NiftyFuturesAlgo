import { MessageSquarePlus, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { TradingJournalEntry, TradingJournalSummary } from '../api/types';
import EmptyState from '../components/ui/EmptyState';
import PageShell from '../components/ui/PageShell';
import { todayIst } from '../utils/dates';
import { formatINR, formatTime } from '../utils/format';

export default function TradingJournal() {
  const [selectedDate, setSelectedDate] = useState<string>(todayIst());
  const [journal, setJournal] = useState<TradingJournalEntry | null>(null);
  const [list, setList] = useState<TradingJournalSummary[]>([]);
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadList = useCallback(() => {
    api.listJournals(30).then((r) => {
      const journals = r.journals ?? [];
      setList(journals);
      if (journals.length > 0 && !journals.find((j) => j.date_ist === selectedDate)) {
        setSelectedDate(journals[0].date_ist!);
      }
    }).catch(() => {});
  }, [selectedDate]);

  const loadJournal = useCallback((date: string) => {
    if (!date) return;
    setLoading(true);
    setError('');
    api.getJournal(date)
      .then((r) => {
        if (r.error) setError(r.error);
        setJournal(r.journal ?? null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (selectedDate) loadJournal(selectedDate);
  }, [selectedDate, loadJournal]);

  const handleRebuild = () => {
    if (!selectedDate) return;
    api.buildJournal(selectedDate).then((r) => {
      if (r.journal) setJournal(r.journal);
      loadList();
    }).catch((e) => setError(String(e)));
  };

  const handleAddNote = () => {
    if (!note.trim() || !selectedDate) return;
    api.addJournalNote(note.trim(), selectedDate).then((r) => {
      if (r.journal) {
        setJournal(r.journal);
        setNote('');
      }
    }).catch((e) => setError(String(e)));
  };

  const summary = journal?.session_summary;
  const risk = summary?.risk_snapshot;
  const feedback = journal?.system_feedback;
  const macro = journal?.macro_context as Record<string, Record<string, unknown>> | undefined;
  const overnight = journal?.overnight_context as Record<string, unknown> | undefined;
  const vix = macro?.vix as Record<string, unknown> | undefined;
  const fii = macro?.fii_dii as Record<string, unknown> | undefined;
  const niftyOh = overnight?.NIFTY as Record<string, unknown> | undefined;
  const hints = overnight?.session_hints as Record<string, unknown> | undefined;

  const dateActions = (
    <div className="page-shell-toolbar">
      <input
        type="date"
        className="input-field input-field--compact"
        value={selectedDate}
        onChange={(e) => setSelectedDate(e.target.value)}
        list="journal-dates"
      />
      <datalist id="journal-dates">
        {list.map((j) => (
          <option key={j.date_ist} value={j.date_ist}>
            {j.quality_score ?? '—'} · {formatINR(j.daily_pnl ?? 0)}
          </option>
        ))}
      </datalist>
      <button type="button" className="btn btn-secondary" onClick={handleRebuild}>
        <RefreshCw size={14} /> Rebuild
      </button>
    </div>
  );

  return (
    <PageShell
      subtitle="Session quality, macro context, and trader notes — one journal per IST trading day."
      actions={dateActions}
      className="page-shell--journal"
    >
      <div className="bento-grid w-full">
        {error && (
          <div className="bento-tile bento-tile--auto bento-tile--accent-brand text-loss journal-col-12">
            {error}
          </div>
        )}

        {loading && (
          <div className="bento-tile bento-tile--auto journal-col-12">
            <EmptyState title="Loading journal…" />
          </div>
        )}

        {journal && !loading && (
          <>
            <div className="bento-tile bento-tile--auto journal-col-4">
              <h3 className="tile-eyebrow">Session Score</h3>
              <p className="tile-metric m-0">
                {summary?.quality_score ?? '—'}
                <span className="tile-metric-sub">Grade {summary?.quality_grade ?? '—'}</span>
              </p>
              <p className={`text-sm m-0 ${(risk?.daily_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                P&L {formatINR(risk?.daily_pnl ?? 0)} · {risk?.trades_today ?? 0} trades
              </p>
            </div>

            <div className="bento-tile bento-tile--auto journal-col-4">
              <h3 className="tile-eyebrow">Macro Context</h3>
              {vix?.available ? (
                <p className="text-sm my-2">
                  India VIX <strong>{String(vix.level)}</strong>
                  <span className="text-muted ml-2">
                    {String(vix.zone)} · {Number(vix.change_pct) >= 0 ? '+' : ''}{String(vix.change_pct)}%
                  </span>
                </p>
              ) : (
                <p className="text-muted text-sm">VIX unavailable</p>
              )}
              {fii?.available ? (
                <p className="text-sm text-muted my-1">
                  FII {Number(fii.fii_net_crores).toLocaleString('en-IN')} Cr ·
                  DII {Number(fii.dii_net_crores).toLocaleString('en-IN')} Cr
                  <br />
                  Bias: <strong className="text-main">{String(fii.flow_bias)}</strong>
                </p>
              ) : (
                <p className="text-muted text-sm">FII/DII unavailable</p>
              )}
            </div>

            <div className="bento-tile bento-tile--auto journal-col-4">
              <h3 className="tile-eyebrow">GIFT Overnight</h3>
              {overnight?.available && niftyOh ? (
                <>
                  <p className="text-sm my-2">
                    Gap <strong>{Number(niftyOh.implied_gap_pct) >= 0 ? '+' : ''}{String(niftyOh.implied_gap_pct)}%</strong>
                    <span className="text-muted ml-2">({String(niftyOh.gap_regime)})</span>
                  </p>
                  <p className="text-sm text-muted m-0">
                    GIFT {String(niftyOh.gift_last)} vs NSE prev {String(niftyOh.nse_prev_close)}
                    {hints?.posture_floor ? (
                      <> · floor <strong className="text-main">{String(hints.posture_floor)}</strong></>
                    ) : null}
                  </p>
                </>
              ) : (
                <p className="text-muted text-sm m-0">
                  No overnight data — run fetch_overnight_context before open.
                </p>
              )}
            </div>

            <div className="bento-tile bento-tile--auto journal-col-12">
              <h3 className="tile-eyebrow">System Feedback</h3>
              <p className="font-semibold mb-2">{feedback?.headline ?? journal.feedback_summary}</p>
              <ul className="m-0 pl-5 text-sm text-muted leading-relaxed">
                {(feedback?.notes ?? []).map((n) => <li key={n}>{n}</li>)}
              </ul>
            </div>

            <div className="bento-tile bento-tile--auto journal-col-6">
              <h3 className="tile-eyebrow">Improvement Actions</h3>
              <ul className="m-0 pl-5 text-sm leading-relaxed">
                {(journal.improvement_actions ?? feedback?.actions ?? []).map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </div>

            <div className="bento-tile bento-tile--auto journal-col-6">
              <h3 className="tile-eyebrow">Closed Trades</h3>
              {(journal.trades ?? []).length === 0 ? (
                <p className="text-muted text-sm m-0">No closed trades recorded.</p>
              ) : (
                <table className="data-table data-table--dense journal-trades-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>P&L</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {journal.trades!.map((t, i) => (
                      <tr key={`${t.symbol}-${i}`}>
                        <td>{t.symbol}</td>
                        <td>{t.side}</td>
                        <td className={(t.realized_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}>
                          {formatINR(t.realized_pnl ?? 0)}
                        </td>
                        <td className="text-muted">{t.exit_reason ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="bento-tile bento-tile--auto journal-col-12">
              <h3 className="tile-eyebrow flex items-center gap-2">
                <MessageSquarePlus size={14} /> Your Notes
              </h3>
              <div className="journal-note-form">
                <input
                  type="text"
                  className="input-field flex-1"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="What worked? What to change tomorrow?"
                  onKeyDown={(e) => e.key === 'Enter' && handleAddNote()}
                />
                <button type="button" className="btn btn-primary" onClick={handleAddNote}>
                  Add
                </button>
              </div>
              {(journal.trader_notes ?? []).length === 0 ? (
                <p className="text-muted text-sm m-0">No notes yet.</p>
              ) : (
                <ul className="note-list">
                  {journal.trader_notes!.map((n, i) => (
                    <li key={`${n.added_at}-${i}`} className="note-list-item">
                      <span className="note-list-meta">
                        {n.added_at ? formatTime(n.added_at) : ''}
                      </span>
                      <div>{n.text}</div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}

        {!journal && !loading && !error && selectedDate && (
          <div className="bento-tile bento-tile--auto journal-col-12">
            <EmptyState
              title={`No journal for ${selectedDate}`}
              message="Click Rebuild to generate from session data, trade ledger, and risk snapshots."
              action={
                <button type="button" className="btn btn-primary" onClick={handleRebuild}>
                  <RefreshCw size={14} /> Rebuild journal
                </button>
              }
            />
          </div>
        )}
      </div>
    </PageShell>
  );
}