export function formatINR(value: number, showSign = false): string {
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (!showSign) return `₹ ${formatted}`;
  if (value > 0) return `+₹ ${formatted}`;
  if (value < 0) return `-₹ ${formatted}`;
  return `₹ ${formatted}`;
}

export function formatPrice(value: number | undefined | null): string {
  if (value == null || value === 0) return '—';
  return value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatTime(ts?: string | number): string {
  if (ts == null || ts === '') return '—';
  try {
    let ms: number;
    if (typeof ts === 'number') {
      ms = ts < 1e12 ? ts * 1000 : ts;
    } else {
      const trimmed = ts.trim();
      const num = Number(trimmed);
      if (!Number.isNaN(num) && trimmed !== '' && !trimmed.includes('-') && !trimmed.includes('T')) {
        ms = num < 1e12 ? num * 1000 : num;
      } else {
        ms = new Date(trimmed).getTime();
      }
    }
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return '—';
  }
}

export function eventLabel(type: string): string {
  const map: Record<string, string> = {
    'signal.accepted': 'ENTRY',
    'signal.rejected': 'REJECTED',
    'order.placed': 'ORDER',
    'order.exit': 'EXIT',
    'order.dry_run': 'DRY-RUN',
  };
  return map[type] ?? type.toUpperCase();
}

export function eventText(exec: {
  type: string;
  side?: string;
  symbol?: string;
  price?: number;
  reason?: string;
  regime?: string;
}): string {
  if (exec.type === 'signal.accepted') {
    return `${exec.side ?? '?'} signal @ ${formatPrice(exec.price)} | regime: ${exec.regime ?? 'normal'}`;
  }
  if (exec.type === 'signal.rejected') {
    const sym = exec.symbol ? `${exec.symbol}: ` : '';
    const reason = exec.reason ?? 'risk gate';
    if (reason.toLowerCase().includes('breakout') || reason.toLowerCase().includes('gate')) {
      return `${sym}${reason}`;
    }
    return `${sym}Rejected — ${reason}`;
  }
  if (exec.type === 'order.placed') {
    return `Order placed: ${exec.side ?? '?'} @ ${formatPrice(exec.price)}`;
  }
  if (exec.type === 'order.exit') {
    return 'Exit order submitted';
  }
  return exec.type;
}