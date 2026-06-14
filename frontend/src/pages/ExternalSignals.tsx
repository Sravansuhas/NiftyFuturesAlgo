import { Save, RefreshCw, Target, BookOpen, Loader2, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import type {
  ExternalJournalRow,
  ExternalJournalStatus,
  ExternalOptionSide,
  ExternalSignalsSheet,
} from '../api/types';
import EmptyState from '../components/ui/EmptyState';
import PageShell from '../components/ui/PageShell';
import { todayIst } from '../utils/dates';
import { formatINR, formatPrice, formatTime } from '../utils/format';

const INDICES = ['SENSEX', 'NIFTY', 'BANKNIFTY'] as const;

const INDEX_HEADER_CLASS: Record<string, string> = {
  NIFTY: 'index-header--nifty',
  BANKNIFTY: 'index-header--banknifty',
  SENSEX: 'index-header--sensex',
};

const JOURNAL_LABELS: Record<ExternalJournalStatus, string> = {
  watching: 'Watching',
  entered: 'In trade',
  target_met: 'Target met',
  stop_hit: 'Stop hit',
  incomplete: 'Incomplete',
  skipped: 'Skipped',
  expired: 'Expired',
};

const EMPTY_SIDE: ExternalOptionSide = {
  entry: null,
  target: null,
  stop_loss: null,
  strike: null,
  journal_status: 'watching',
};

function blankSheet(date: string): ExternalSignalsSheet {
  return {
    date,
    notes: '',
    indices: Object.fromEntries(
      INDICES.map((idx) => [idx, { call: { ...EMPTY_SIDE }, put: { ...EMPTY_SIDE } }]),
    ) as ExternalSignalsSheet['indices'],
  };
}

type PageTab = 'sheet' | 'journal';

function PnlRow({ label, value, positive }: { label: string; value: number | null | undefined; positive?: boolean }) {
  if (value == null) return null;
  const up = positive ?? value >= 0;
  return (
    <div className="flex justify-between text-xxs">
      <span className="text-muted">{label}</span>
      <span className={`font-mono font-semibold ${up ? 'text-profit' : 'text-loss'}`}>
        {formatINR(value, true)}
      </span>
    </div>
  );
}

function SidePnlPanel({ side }: { side: ExternalOptionSide }) {
  if (!side.strike && side.entry == null) return null;
  const inTrade = side.journal_status === 'entered' || side.journal_status === 'target_met' || side.journal_status === 'stop_hit';

  return (
    <div className="pnl-panel">
      <div className="text-2xs font-bold text-muted uppercase">1 Lot P&L (after est. taxes)</div>
      {side.lot_size != null && (
        <div className="text-xxs text-muted">
          Lot size: <strong>{side.lot_size}</strong>
          {side.premium != null && (
            <> · Premium <span className="font-mono">{formatPrice(side.premium)}</span></>
          )}
          {side.lot_price_inr != null && (
            <> · Lot price <span className="font-mono font-bold">{formatINR(side.lot_price_inr)}</span></>
          )}
        </div>
      )}
      <PnlRow label="Max gain @ target (net)" value={side.gain_net_1lot} positive />
      <PnlRow label="Max loss @ stop (net)" value={side.loss_net_1lot} positive={false} />
      {inTrade && <PnlRow label="MTM now (net)" value={side.mtm_net_1lot} />}
      {side.costs_round_turn != null && (
        <div className="text-2xs text-muted mt-0.5">
          Est. round-turn cost: {formatINR(side.costs_round_turn)} (brokerage + STT 0.15% + charges)
        </div>
      )}
    </div>
  );
}

function JournalBadge({ status }: { status?: ExternalJournalStatus }) {
  const s = status ?? 'watching';
  return (
    <span className={`journal-badge journal-badge--${s}`}>
      {JOURNAL_LABELS[s]}
    </span>
  );
}

function SideBlock({
  label,
  side,
  prefix,
  onChange,
}: {
  label: string;
  side: ExternalOptionSide;
  prefix: 'C' | 'P';
  onChange: (patch: Partial<ExternalOptionSide>) => void;
}) {
  const num = (v: number | null | undefined) => (v == null ? '' : String(v));

  return (
    <div className="flex flex-col gap-2 mb-4">
      <div className="flex justify-between items-center">
        <span className="text-2xs font-bold text-muted uppercase">{label}</span>
        <JournalBadge status={side.journal_status} />
      </div>
      <div className="field-grid field-grid--quad">
        <label className="field-label">
          Strike
          <input
            className="input-field font-mono font-bold"
            type="number"
            value={num(side.strike)}
            onChange={(e) => onChange({ strike: e.target.value === '' ? null : Number(e.target.value) })}
            placeholder="23100"
          />
        </label>
        <label className="field-label">
          {prefix} Entry
          <input
            className="input-field input-field--compact"
            type="number"
            value={num(side.entry)}
            onChange={(e) => onChange({ entry: e.target.value === '' ? null : Number(e.target.value) })}
            placeholder={`${prefix}180`}
          />
        </label>
        <label className="field-label">
          T Target
          <input
            className="input-field input-field--compact"
            type="number"
            value={num(side.target)}
            onChange={(e) => onChange({ target: e.target.value === '' ? null : Number(e.target.value) })}
            placeholder="T230"
          />
        </label>
        <label className="field-label">
          L Stop
          <input
            className="input-field input-field--compact"
            type="number"
            value={num(side.stop_loss)}
            onChange={(e) => onChange({ stop_loss: e.target.value === '' ? null : Number(e.target.value) })}
            placeholder="L7"
          />
        </label>
      </div>
      {(side.last_ltp != null || side.outcome_note) && (
        <div className="hint-box text-xxs">
          {side.last_ltp != null && (
            <div>
              LTP {formatPrice(side.last_ltp)}
              {side.session_high != null && ` · H ${formatPrice(side.session_high)}`}
              {side.session_low != null && ` · L ${formatPrice(side.session_low)}`}
              {side.entry_fill != null && ` · fill ${formatPrice(side.entry_fill)}`}
            </div>
          )}
          {side.outcome_note && <div className="mt-1">{side.outcome_note}</div>}
          {side.checked_at && (
            <div className="mt-1 opacity-80">Checked {formatTime(side.checked_at)}</div>
          )}
        </div>
      )}
      <SidePnlPanel side={side} />
    </div>
  );
}

function JournalTable({ rows }: { rows: ExternalJournalRow[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No journal entries yet"
        message='Save a daily sheet first, then use "Check targets" during market hours.'
      />
    );
  }

  return (
    <div className="overflow-x-auto scroll-panel scroll-panel-lg">
      <table className="data-table data-table--dense">
        <thead>
          <tr>
            <th>Date</th>
            <th>Index</th>
            <th>Leg</th>
            <th>Strike</th>
            <th>C/P/T/L</th>
            <th>Status</th>
            <th>LTP / H / L</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.date}-${r.index}-${r.leg}-${i}`}>
              <td className="whitespace-nowrap">{r.date}</td>
              <td>{r.display_name ?? r.index}</td>
              <td className="font-semibold">{r.option_type}</td>
              <td className="font-mono font-bold">{r.strike ?? '—'}</td>
              <td className="font-mono whitespace-nowrap">
                {formatPrice(r.entry)} / {formatPrice(r.target)} / L{r.stop_loss ?? '—'}
              </td>
              <td>
                <JournalBadge status={r.journal_status} />
              </td>
              <td className="font-mono whitespace-nowrap">
                {formatPrice(r.last_ltp)} / {formatPrice(r.session_high)} / {formatPrice(r.session_low)}
              </td>
              <td className="text-muted" style={{ maxWidth: '280px' }}>
                {r.outcome_note || '—'}
                {r.target_met_at && (
                  <div className="text-2xs mt-0.5">Target {formatTime(r.target_met_at)}</div>
                )}
                {r.stop_hit_at && (
                  <div className="text-2xs mt-0.5">Stop {formatTime(r.stop_hit_at)}</div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ExternalSignals() {
  const [sheet, setSheet] = useState<ExternalSignalsSheet | null>(null);
  const [displayNames, setDisplayNames] = useState<Record<string, string>>({});
  const [tradeDate, setTradeDate] = useState(todayIst());
  const [savedDates, setSavedDates] = useState<string[]>([]);
  const [premiums, setPremiums] = useState<Record<string, unknown> | null>(null);
  const [journalRows, setJournalRows] = useState<ExternalJournalRow[]>([]);
  const [journalDate, setJournalDate] = useState(todayIst());
  const [journalAllDates, setJournalAllDates] = useState(false);
  const [activeTab, setActiveTab] = useState<PageTab>('sheet');
  const [saving, setSaving] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [lastEvalAt, setLastEvalAt] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);

  const markDirty = useCallback(() => {
    dirtyRef.current = true;
    setDirty(true);
  }, []);

  const clearDirty = useCallback(() => {
    dirtyRef.current = false;
    setDirty(false);
  }, []);

  const loadJournal = useCallback(async (date?: string, allDates?: boolean) => {
    const showAll = allDates ?? journalAllDates;
    const filterDate = showAll ? undefined : (date ?? journalDate);
    try {
      const res = await api.getExternalSignalJournal(90, filterDate);
      setJournalRows(res.rows ?? []);
      if (res.dates?.length) setSavedDates(res.dates);
    } catch (e) {
      setMessage(`Journal load failed: ${e}`);
    }
  }, [journalAllDates, journalDate]);

  const load = useCallback(async (date: string) => {
    setLoading(true);
    setMessage(null);
    try {
      const [res, datesRes] = await Promise.all([
        api.getExternalSignals(date),
        api.getExternalSignalDates(),
      ]);
      setSheet(res.sheet);
      setDisplayNames(res.display_names ?? {});
      setSavedDates(datesRes.dates ?? []);
      setTradeDate(date);
      clearDirty();
    } catch (e) {
      setMessage(`Failed to load: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [clearDirty]);

  const checkTargets = useCallback(async (silent = false) => {
    if (silent && dirtyRef.current) return;
    if (!silent) setEvaluating(true);
    if (!silent) setMessage(null);
    try {
      const res = await api.evaluateExternalSignals(tradeDate);
      if (!res.ok) throw new Error(res.error ?? 'evaluate failed');
      if (!dirtyRef.current) {
        setSheet({ ...res.sheet, pnl_summary: res.pnl_summary ?? res.sheet.pnl_summary });
      }
      setPremiums(res.premiums as Record<string, unknown>);
      if (!journalAllDates) {
        setJournalRows(res.journal_rows ?? []);
      }
      setLastEvalAt(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true }));
      if (!silent) {
        const met = (res.journal_rows ?? []).filter((r) => r.journal_status === 'target_met').length;
        const stops = (res.journal_rows ?? []).filter((r) => r.journal_status === 'stop_hit').length;
        setMessage(`Targets checked — ${met} hit target, ${stops} hit stop`);
      }
    } catch (e) {
      if (!silent) setMessage(`Check failed: ${e}`);
    } finally {
      if (!silent) setEvaluating(false);
    }
  }, [tradeDate, journalAllDates]);

  useEffect(() => {
    load(tradeDate);
  }, [tradeDate, load]);

  useEffect(() => {
    if (activeTab === 'journal') loadJournal();
  }, [activeTab, loadJournal, journalDate, journalAllDates]);

  useEffect(() => {
    if (activeTab === 'journal' && !journalAllDates) {
      setJournalDate(tradeDate);
    }
  }, [activeTab, tradeDate, journalAllDates]);

  useEffect(() => {
    const isToday = tradeDate === todayIst();
    if (activeTab !== 'sheet' || !isToday) return undefined;
    const id = window.setInterval(() => {
      checkTargets(true);
    }, 30_000);
    return () => window.clearInterval(id);
  }, [activeTab, tradeDate, checkTargets]);

  const updateSide = (
    index: typeof INDICES[number],
    leg: 'call' | 'put',
    patch: Partial<ExternalOptionSide>,
  ) => {
    if (!sheet) return;
    markDirty();
    setSheet({
      ...sheet,
      date: tradeDate,
      indices: {
        ...sheet.indices,
        [index]: {
          ...sheet.indices[index],
          [leg]: { ...sheet.indices[index][leg], ...patch },
        },
      },
    });
  };

  const save = async () => {
    if (!sheet) return;
    setSaving(true);
    setMessage(null);
    try {
      const payload: ExternalSignalsSheet = {
        ...sheet,
        date: tradeDate,
        notes: sheet.notes ?? '',
        indices: sheet.indices,
      };
      const res = await api.saveExternalSignals(payload);
      setSheet(res.sheet);
      clearDirty();
      setJournalDate(res.sheet.date);
      const savedRows = res.journal_rows ?? [];
      if (savedRows.length > 0 && !journalAllDates) {
        setJournalRows(savedRows);
      }
      const indicesSaved = new Set(savedRows.map((r) => r.index)).size;
      const legHint = savedRows.length > 0
        ? ` — ${savedRows.length} leg(s) across ${indicesSaved} index(es) in journal`
        : '';
      setMessage(`Saved for ${res.sheet.date}${legHint}`);
      const datesRes = await api.getExternalSignalDates();
      setSavedDates(datesRes.dates ?? []);
      if (journalAllDates || activeTab === 'journal') {
        await loadJournal(res.sheet.date, journalAllDates);
      }
    } catch (e) {
      setMessage(`Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  const refreshPremiums = async () => {
    if (dirtyRef.current) {
      setMessage('Save or reload first — unsaved edits are protected from premium refresh.');
      return;
    }
    setMessage(null);
    try {
      const res = await api.getExternalSignalPremiums(tradeDate);
      setPremiums(res.premiums as Record<string, unknown>);
      if (res.sheet && !dirtyRef.current) setSheet(res.sheet);
    } catch (e) {
      setMessage(`Premium fetch failed: ${e}`);
    }
  };

  const deleteDay = async () => {
    if (!window.confirm(`Delete saved sheet for ${tradeDate}? This cannot be undone.`)) return;
    setMessage(null);
    try {
      const res = await api.deleteExternalSignals(tradeDate);
      setSheet(res.sheet);
      clearDirty();
      const datesRes = await api.getExternalSignalDates();
      setSavedDates(datesRes.dates ?? []);
      setJournalRows([]);
      setMessage(res.deleted ? `Deleted sheet for ${tradeDate}` : `No saved sheet for ${tradeDate} — showing blank`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 405) {
        try {
          const res = await api.saveExternalSignals(blankSheet(tradeDate));
          setSheet(res.sheet);
          clearDirty();
          setJournalRows([]);
          setMessage(
            `Cleared ${tradeDate} (dashboard needs restart for full delete — restart run.py, then Delete day again)`,
          );
          return;
        } catch (clearErr) {
          setMessage(`Clear failed: ${clearErr}`);
          return;
        }
      }
      setMessage(`Delete failed: ${e}`);
    }
  };

  const tabActions = (
    <>
      <div className="bt-tabs m-0 border-b-0 mb-0">
        <button
          type="button"
          className={`bt-tab ${activeTab === 'sheet' ? 'bt-tab-active' : ''}`}
          onClick={() => setActiveTab('sheet')}
        >
          Today&apos;s sheet
        </button>
        <button
          type="button"
          className={`bt-tab ${activeTab === 'journal' ? 'bt-tab-active' : ''}`}
          onClick={() => setActiveTab('journal')}
        >
          <BookOpen size={14} className="mr-2" style={{ verticalAlign: 'middle' }} />
          Journal
        </button>
      </div>
      {activeTab === 'sheet' && (
        <>
          <input
            type="date"
            className="input-field input-field--compact"
            style={{ width: 'auto' }}
            value={tradeDate}
            onChange={(e) => {
              const next = e.target.value;
              if (dirty && next !== tradeDate && !window.confirm('Unsaved changes will be lost. Switch date anyway?')) {
                return;
              }
              setTradeDate(next);
            }}
          />
          <button className="btn btn-secondary" onClick={() => load(tradeDate)} disabled={loading || !dirty}>
            <RefreshCw size={16} /> Reload
          </button>
          <button className="btn btn-secondary" onClick={refreshPremiums}>
            Kite premiums
          </button>
          <button className="btn btn-primary" onClick={() => checkTargets(false)} disabled={evaluating}>
            {evaluating ? <Loader2 size={16} className="animate-spin" /> : <Target size={16} />}
            {evaluating ? 'Checking…' : 'Check targets'}
          </button>
          <button className="btn btn-primary" onClick={save} disabled={saving || !dirty}>
            <Save size={16} /> {saving ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
          </button>
          <button className="btn btn-secondary" onClick={deleteDay} disabled={!savedDates.includes(tradeDate)}>
            <Trash2 size={16} /> Delete day
          </button>
        </>
      )}
      {activeTab === 'journal' && (
        <>
          <input
            type="date"
            className="input-field input-field--compact"
            style={{ width: 'auto' }}
            value={journalDate}
            disabled={journalAllDates}
            onChange={(e) => setJournalDate(e.target.value)}
          />
          <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={journalAllDates}
              onChange={(e) => setJournalAllDates(e.target.checked)}
            />
            All dates
          </label>
          <button className="btn btn-secondary" onClick={() => loadJournal()}>
            <RefreshCw size={16} /> Refresh journal
          </button>
        </>
      )}
    </>
  );

  if (loading && !sheet) {
    return (
      <PageShell subtitle="Enter CE/PE levels each morning — auto-tracks targets vs live Kite premiums.">
        <EmptyState variant="centered" title="Loading options sheet…" />
      </PageShell>
    );
  }

  return (
    <PageShell
      subtitle="Load a date → edit strike / C·T·L → Save (updates JSON). Delete day removes that date. Auto target-check pauses while you have unsaved edits."
      actions={tabActions}
    >
      <div className="bento-grid">
        {(message || (activeTab === 'sheet' && lastEvalAt) || (savedDates.length > 0 && activeTab === 'sheet')) && (
          <div className="bento-tile" style={{ gridColumn: 'span 12' }}>
            {message && <p className="message-banner message-banner--info mb-2">{message}</p>}
            {activeTab === 'sheet' && dirty && (
              <p className="text-xs text-warning m-0 mb-2 font-semibold">Unsaved changes — click Save changes before leaving this date.</p>
            )}
            {activeTab === 'sheet' && lastEvalAt && (
              <p className="text-xs text-muted m-0">
                Last target check: {lastEvalAt}
                {tradeDate === todayIst() ? ' · auto-refresh every 30s' : ''}
              </p>
            )}
            {savedDates.length > 0 && activeTab === 'sheet' && (
              <p className="text-xs text-muted mt-1 mb-0">
                Saved dates: {savedDates.slice(0, 8).join(', ')}
                {savedDates.length > 8 ? '…' : ''}
              </p>
            )}
          </div>
        )}

        {activeTab === 'sheet' && sheet?.pnl_summary && sheet.pnl_summary.legs != null && sheet.pnl_summary.legs > 0 && (
          <div className="bento-tile" style={{ gridColumn: 'span 12' }}>
            <div className="pnl-summary-grid">
              <div>
                <div className="pnl-summary-label">Options MTM (net)</div>
                <div className={`font-mono text-lg font-bold ${(sheet.pnl_summary.mtm_net ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {formatINR(sheet.pnl_summary.mtm_net ?? 0, true)}
                </div>
              </div>
              <div>
                <div className="pnl-summary-label">If all hit target</div>
                <div className="font-mono text-profit font-semibold">
                  {formatINR(sheet.pnl_summary.max_gain_net_if_all_hit ?? 0, true)}
                </div>
              </div>
              <div>
                <div className="pnl-summary-label">If all hit stop</div>
                <div className="font-mono text-loss font-semibold">
                  {formatINR(sheet.pnl_summary.max_loss_net_if_all_stop ?? 0, true)}
                </div>
              </div>
              <div>
                <div className="pnl-summary-label">Legs in trade</div>
                <div className="font-mono font-semibold">
                  {sheet.pnl_summary.in_trade ?? 0} / {sheet.pnl_summary.legs}
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'journal' && (
          <div className="bento-tile" style={{ gridColumn: 'span 12' }}>
            <h3 className="tile-title text-base mb-1">
              Options journal{journalAllDates ? ' — all dates' : ` — ${journalDate}`}
            </h3>
            <p className="text-xs text-muted mb-3 m-0">
              {journalAllDates
                ? 'Showing every saved leg across all trade dates.'
                : 'Showing legs with entry, target, stop, or strike for the selected date. Fill all three index tiles on the sheet tab, then Save.'}
            </p>
            <JournalTable rows={journalRows} />
          </div>
        )}

        {activeTab === 'sheet' && sheet && INDICES.map((idx) => {
          const block = sheet.indices[idx] ?? { call: EMPTY_SIDE, put: EMPTY_SIDE };
          const live = (premiums as { indices?: Record<string, { call_ltp?: number; put_ltp?: number; error?: string }> })?.indices?.[idx];
          return (
            <div key={idx} className="bento-tile" style={{ gridColumn: 'span 4' }}>
              <div className={`index-header ${INDEX_HEADER_CLASS[idx]}`}>
                {displayNames[idx] ?? idx}
              </div>
              <SideBlock
                label="Call (bullish)"
                prefix="C"
                side={block.call ?? EMPTY_SIDE}
                onChange={(p) => updateSide(idx, 'call', p)}
              />
              <SideBlock
                label="Put (bearish)"
                prefix="P"
                side={block.put ?? EMPTY_SIDE}
                onChange={(p) => updateSide(idx, 'put', p)}
              />
              {live && !live.error && (
                <div className="text-xs text-muted border-t border-dim pt-3">
                  Kite LTP: CE {formatPrice(live.call_ltp)} · PE {formatPrice(live.put_ltp)}
                </div>
              )}
              {live?.error && (
                <div className="text-xs text-muted">Kite: {live.error}</div>
              )}
            </div>
          );
        })}

        {activeTab === 'sheet' && sheet && (
          <div className="bento-tile" style={{ gridColumn: 'span 12' }}>
            <label className="field-label text-sm">
              Day notes
              <textarea
                className="input-field mt-2"
                rows={2}
                value={sheet.notes ?? ''}
                onChange={(e) => {
                  markDirty();
                  setSheet({ ...sheet, notes: e.target.value });
                }}
                style={{ resize: 'vertical' }}
                placeholder="Optional notes for this session"
              />
            </label>
          </div>
        )}
      </div>
    </PageShell>
  );
}