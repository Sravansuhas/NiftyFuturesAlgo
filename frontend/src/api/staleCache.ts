/** In-memory stale-while-revalidate cache — survives React route remounts within one tab session. */

type Entry<T> = { data: T; fetchedAt: number };

const store = new Map<string, Entry<unknown>>();

export function readStaleCache<T>(key: string, maxAgeMs: number): T | null {
  const hit = store.get(key);
  if (!hit) return null;
  if (Date.now() - hit.fetchedAt > maxAgeMs) return null;
  return hit.data as T;
}

export function writeStaleCache<T>(key: string, data: T): void {
  store.set(key, { data, fetchedAt: Date.now() });
}

export async function fetchWithStaleCache<T>(
  key: string,
  maxAgeMs: number,
  fetcher: () => Promise<T>,
  options?: { backgroundRefresh?: boolean },
): Promise<T> {
  const cached = readStaleCache<T>(key, maxAgeMs);
  if (cached != null) {
    if (options?.backgroundRefresh !== false) {
      void fetcher()
        .then((fresh) => writeStaleCache(key, fresh))
        .catch(() => {});
    }
    return cached;
  }
  const fresh = await fetcher();
  writeStaleCache(key, fresh);
  return fresh;
}