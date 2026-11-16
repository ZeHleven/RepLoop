// Read-only, memory-only snapshots. No auth tokens, persisted personal data or writes.
const FRESH_MS = 30_000
const SNAPSHOT_MS = 300_000
const MAX_ENTRIES = 40
type Stamp = { session: number, revision: number, day: string }
type Entry = { stamp: Stamp, at: number, value: unknown, failed?: boolean }
const entries = new Map<string, Entry>()
const pending = new Map<string, { stamp: Stamp, promise: Promise<unknown> }>()
let session = 0
let revision = 0

export function readCacheDay (): string {
  return new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
}
export function readCacheSession (): number { return session }
const stamp = (): Stamp => ({ session, revision, day: readCacheDay() })
const matches = (value: Stamp) => value.session === session && value.revision === revision && value.day === readCacheDay()
const copy = <T>(value: T): T => value === undefined ? value : JSON.parse(JSON.stringify(value))

export function invalidateReadCache (): void {
  revision++; entries.clear(); pending.clear()
}
export function resetReadCacheSession (): void {
  session++; invalidateReadCache()
}
export function peekCached<T> (key: string): T | undefined {
  const value = entries.get(key)
  const age = value ? Date.now() - value.at : -1
  if (!value || !matches(value.stamp) || age < 0 || age > SNAPSHOT_MS) return undefined
  return copy(value.value as T)
}
export function isCachedFresh (key: string): boolean {
  const value = entries.get(key)
  const age = value ? Date.now() - value.at : -1
  return Boolean(value && !value.failed && matches(value.stamp) && age >= 0 && age < FRESH_MS)
}

export async function readCached<T> (
  key: string, fetcher: () => Promise<T>, force = false, retries = 0
): Promise<T> {
  if (!force && isCachedFresh(key)) return peekCached<T>(key) as T
  const flight = pending.get(key)
  if (flight && matches(flight.stamp)) return copy(await flight.promise as T)
  const requestStamp = stamp()
  const promise = (async () => {
    // Promise boundary also captures a synchronously throwing transport.
    const value = await Promise.resolve().then(fetcher)
    if (requestStamp.session !== session) throw new Error('登录状态已改变，请在当前账号下重新操作')
    if (!matches(requestStamp)) {
      if (retries >= 2) throw new Error('数据正在更新，请稍后重试')
      return readCached(key, fetcher, true, retries + 1)
    }
    entries.delete(key)
    entries.set(key, { stamp: requestStamp, at: Date.now(), value: copy(value) })
    while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value!)
    return value
  })()
  pending.set(key, { stamp: requestStamp, promise })
  try { return copy(await promise) }
  catch (error) {
    if (matches(requestStamp)) {
      const value = entries.get(key)
      if (value) value.failed = true
    }
    throw error
  } finally {
    if (pending.get(key)?.promise === promise) pending.delete(key)
  }
}
