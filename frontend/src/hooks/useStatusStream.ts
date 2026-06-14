import { useEffect, useRef, useState } from 'react';
import type { StatusStreamPayload } from '../api/types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export function useStatusStream(enabled = true) {
  const [data, setData] = useState<StatusStreamPayload | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!enabled) return;

    const url = `${API_BASE}/api/status/stream`;
    const source = new EventSource(url);
    sourceRef.current = source;

    source.onopen = () => {
      setConnected(true);
      setError(null);
    };

    source.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as StatusStreamPayload;
        setData(parsed);
      } catch {
        setError('Failed to parse live stream');
      }
    };

    source.onerror = () => {
      setConnected(false);
      setError('Live stream disconnected — is python run.py active?');
    };

    return () => {
      source.close();
      sourceRef.current = null;
    };
  }, [enabled]);

  return { data, connected, error };
}