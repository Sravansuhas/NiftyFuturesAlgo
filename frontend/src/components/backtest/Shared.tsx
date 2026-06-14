import { Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';

export type BacktestTab = 'run' | 'results' | 'learnings' | 'fills' | 'data';

const TABS: { id: BacktestTab; label: string }[] = [
  { id: 'run', label: 'Run Validation' },
  { id: 'results', label: 'Results' },
  { id: 'learnings', label: 'Learnings' },
  { id: 'fills', label: 'Real Fills' },
  { id: 'data', label: 'Data' },
];

export function TabBar({ active, onChange }: { active: BacktestTab; onChange: (t: BacktestTab) => void }) {
  return (
    <div className="bt-tabs">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          className={`bt-tab ${active === t.id ? 'bt-tab-active' : ''}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Panel({ children, className = '', style }: { children: ReactNode; className?: string; style?: React.CSSProperties }) {
  return <div className={`bento-tile ${className}`} style={style}>{children}</div>;
}

export function SectionTitle({ children, sub }: { children: ReactNode; sub?: string }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>{children}</h3>
      {sub && <p className="text-muted" style={{ fontSize: '0.75rem', marginTop: 4 }}>{sub}</p>}
    </div>
  );
}

export function ProgressBar({ value, label, accent = 'var(--brand-primary)' }: { value: number; label?: string; accent?: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      {label && (
        <div className="font-mono text-muted" style={{ fontSize: '0.7rem', marginBottom: 6 }}>
          {label}
        </div>
      )}
      <div className="bt-progress-track">
        <div className="bt-progress-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: accent }} />
      </div>
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    healthy: 'bt-badge-ok',
    stale: 'bt-badge-warn',
    corrupt: 'bt-badge-bad',
    missing: 'bt-badge-muted',
    running: 'bt-badge-warn',
    completed: 'bt-badge-ok',
    failed: 'bt-badge-bad',
    cancelled: 'bt-badge-muted',
    READY: 'bt-badge-muted',
  };
  return <span className={`bt-badge ${map[status] ?? 'bt-badge-muted'}`}>{status.toUpperCase()}</span>;
}

export function KpiGrid({ items }: { items: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="bt-kpi-grid">
      {items.map((k) => (
        <div key={k.label} className="bt-kpi">
          <div className="text-muted" style={{ fontSize: '0.7rem', textTransform: 'uppercase', marginBottom: 6 }}>{k.label}</div>
          <div className="font-mono" style={{ fontSize: '1.2rem', fontWeight: 700, color: k.color ?? 'var(--text-main)' }}>{k.value}</div>
        </div>
      ))}
    </div>
  );
}

export function Spinner({ size = 20 }: { size?: number }) {
  return <Loader2 size={size} style={{ animation: 'spin 1s linear infinite' }} />;
}

export function FieldLabel({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <label className="text-muted" style={{ fontSize: '0.75rem', display: 'block', marginBottom: 6 }}>
      {children}
      {hint && <span style={{ marginLeft: 6, opacity: 0.6 }} title={hint}>ⓘ</span>}
    </label>
  );
}

export function CheckboxRow({
  checked,
  onChange,
  label,
  accent,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  accent?: string;
}) {
  return (
    <label className="bt-check-row">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ accentColor: accent ?? 'var(--brand-primary)' }} />
      <span style={{ fontSize: '0.875rem' }}>{label}</span>
    </label>
  );
}