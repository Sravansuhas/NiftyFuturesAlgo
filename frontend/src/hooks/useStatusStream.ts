import { useEffect, useRef, useState } from 'react';
import type { StatusStreamPayload } from '../api/types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';
/** No data frame for this long → show "Polling" instead of "Live SSE" in header. */
const STALE_MS = 45000;

export function useStatusStream(enabled = true) {
  const [data, setData] = useState<StatusStreamPayload | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const lastFreshRef = useRef(0);

  useEffect(() => {
    if (!enabled) return undefined;

    const markFresh = () => {
      lastFreshRef.current = Date.now();
      setConnected(true);
      setError(null);
    };

    const url = `${API_BASE}/api/status/stream`;
    const source = new EventSource(url);
    sourceRef.current = source;

    source.onopen = () => {
      markFresh();
    };

    source.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as StatusStreamPayload;
        setData(parsed);
        markFresh();
      } catch {
        setError('Failed to parse live stream');
      }
    };

    // EventSource auto-reconnects — do not close() or spin manual reconnect loops.
    source.onerror = () => {
      if (Date.now() - lastFreshRef.current > STALE_MS) {
        setConnected(false);
        setError('Live stream idle — REST polling continues');
      }
    };

    const watchdog = window.setInterval(() => {
      if (Date.now() - lastFreshRef.current > STALE_MS) {
        setConnected(false);
      }
    }, 5000);

    return () => {
      window.clearInterval(watchdog);
      source.close();
      sourceRef.current = null;
    };
  }, [enabled]);

  return { data, connected, error };
}