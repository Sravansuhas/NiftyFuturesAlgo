/** Trade date in IST (YYYY-MM-DD), aligned with backend now_ist(). */
export function todayIst(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
}

/** Live clock for header rail — e.g. "Monday, 15 June 2026 · 10:32:45 IST". */
export function formatIstClock(now: Date = new Date()): string {
  const datePart = now.toLocaleDateString('en-IN', {
    timeZone: 'Asia/Kolkata',
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
  const timePart = now.toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  return `${datePart} · ${timePart} IST`;
}