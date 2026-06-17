import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { api } from '../api/client';
import OptionsAlgoPanel from '../components/OptionsAlgoPanel';
import type { StatusStreamPayload, SystemStatus } from '../api/types';

interface OutletContext {
  status: SystemStatus | null;
  stream: StatusStreamPayload | null;
  engineOnline: boolean;
  connectivity: import('../hooks/useEngineConnectivity').EngineConnectivity;
}

export default function Dashboard() {
  const { status, stream, engineOnline } = useOutletContext<OutletContext>();
  const [recentTrades, setRecentTrades] = useState<Array<Record<string, unknown>>>([]);
  const marketOpen = status?.market?.is_market_open;

  useEffect(() => {
    const load = () => {
      api.getTradesCached(40)
        .then((r) => setRecentTrades(r.trades ?? []))
        .catch(() => setRecentTrades([]));
    };
    load();
    const pollMs = marketOpen === false ? 30000 : 15000;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [marketOpen]);

  const tokenOk = status?.token_valid !== false;
  const recentExec =
    (stream?.recent_execution?.length ? stream.recent_execution : status?.recent_execution) ?? [];
  const sessionStatus = status?.market?.session_status;

  return (
    <div className="bento-grid dashboard-grid dashboard-grid--algo">
      {!tokenOk && engineOnline && (
        <div className="dashboard-alert dashboard-alert--error">
          <strong>Token expired.</strong> Renew in <strong>Settings → Auto Login</strong> before market open.
        </div>
      )}

      <div className="dashboard-algo-shell">
        <OptionsAlgoPanel
          status={status}
          recentExecutions={recentExec}
          recentTrades={recentTrades}
          marketOpen={marketOpen}
          sessionStatus={sessionStatus}
        />
      </div>
    </div>
  );
}