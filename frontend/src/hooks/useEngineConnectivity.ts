import type { SystemStatus } from '../api/types';

export type EngineConnectivity =
  | 'loading'
  | 'api_down'
  | 'engine_offline'
  | 'degraded'
  | 'connected';

export function deriveEngineConnectivity(
  apiReachable: boolean,
  healthEngineReady: boolean,
  statusLoading: boolean,
  status: SystemStatus | null,
  streamConnected: boolean,
): EngineConnectivity {
  if (!apiReachable) return 'api_down';
  if (statusLoading && !status) return 'loading';
  if (!status) {
    return healthEngineReady ? 'connected' : 'engine_offline';
  }
  if (status.error || status.engine_ready === false || !healthEngineReady) {
    return 'engine_offline';
  }
  // REST status polling is authoritative — SSE is an enhancement, not required for "online".
  void streamConnected;
  return 'connected';
}

export function isEngineOnline(
  connectivity: EngineConnectivity,
): boolean {
  return connectivity === 'connected' || connectivity === 'degraded';
}