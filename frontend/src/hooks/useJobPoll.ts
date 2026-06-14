import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { BacktestJob } from '../api/types';

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

export function useJobPoll(intervalMs = 1200) {
  const [job, setJob] = useState<BacktestJob | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const start = useCallback(
    (id: string) => {
      stop();
      setJobId(id);
      setJob({ status: 'running', progress: 0, stage: 'queued' });

      pollRef.current = setInterval(async () => {
        try {
          const result = await api.getBacktestResult(id);
          setJob(result);
          if (TERMINAL.has(result.status)) stop();
        } catch {
          /* transient network */
        }
      }, intervalMs);
    },
    [intervalMs, stop]
  );

  const cancel = useCallback(async () => {
    if (jobId) await api.cancelBacktest(jobId);
    stop();
  }, [jobId, stop]);

  const reset = useCallback(() => {
    stop();
    setJob(null);
    setJobId(null);
  }, [stop]);

  useEffect(() => () => stop(), [stop]);

  return {
    job,
    jobId,
    isRunning: !!job && !TERMINAL.has(job.status),
    start,
    stop,
    cancel,
    reset,
  };
}