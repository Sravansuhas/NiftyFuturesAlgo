/** Trade date in IST (YYYY-MM-DD), aligned with backend now_ist(). */
export function todayIst(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
}