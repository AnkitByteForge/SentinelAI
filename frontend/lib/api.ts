const BASE   = '/api/gateway'
const HEADERS = { 'Authorization': 'Bearer sentinel-dev-key-123', 'Content-Type': 'application/json' }

// ── Types ─────────────────────────────────────────────────────────────

export interface Metrics {
  window: string
  total_requests: number
  successful_requests: number
  failed_requests: number
  fallback_requests: number
  cache_hit_rate: number
  total_cost_usd: number
  latency: { p50_ms: number; p95_ms: number; p99_ms: number; avg_ms: number }
  providers: Record<string, { requests: number; errors: number; avg_latency_ms: number; error_rate: number }>
}

export interface LogEntry {
  id: string
  provider: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  latency_ms: number
  cache_hit: boolean
  status: string
  error_type: string | null
  fallback_from: string | null
  created_at: string
}

export interface LogsResponse {
  total: number
  page: number
  limit: number
  logs: LogEntry[]
}

export interface CacheStats {
  total_entries: number
  total_hits: number
  total_saved_usd: number
  threshold: number
  top_cached: Array<{ prompt_preview: string; hit_count: number; saved_usd: number }>
}

export interface CircuitState {
  state: 'closed' | 'open' | 'half_open'
  failure_count: number
  last_failure: number
}

// ── API functions ──────────────────────────────────────────────────────

export async function fetchMetrics(window = '24h'): Promise<Metrics> {
  const res = await fetch(`${BASE}/v1/metrics?window=${window}`, { headers: HEADERS, cache: 'no-store' })
  if (!res.ok) throw new Error('Failed to fetch metrics')
  return res.json()
}

export async function fetchLogs(params?: { limit?: number; status?: string; provider?: string }): Promise<LogsResponse> {
  const q = new URLSearchParams()
  if (params?.limit)    q.set('limit',    String(params.limit))
  if (params?.status)   q.set('status',   params.status)
  if (params?.provider) q.set('provider', params.provider)
  const res = await fetch(`${BASE}/v1/logs?${q}`, { headers: HEADERS, cache: 'no-store' })
  if (!res.ok) throw new Error('Failed to fetch logs')
  return res.json()
}

export async function fetchCacheStats(): Promise<CacheStats> {
  const res = await fetch(`${BASE}/v1/cache/stats`, { headers: HEADERS, cache: 'no-store' })
  if (!res.ok) throw new Error('Failed to fetch cache stats')
  return res.json()
}

export async function fetchCircuitStates(): Promise<Record<string, CircuitState>> {
  const res = await fetch(`${BASE}/v1/circuit/states`, { headers: HEADERS, cache: 'no-store' })
  if (!res.ok) return {}
  return res.json()
}

export async function fetchHealth(): Promise<{ status: string; circuit_breakers: Record<string, CircuitState> }> {
  const res = await fetch(`${BASE}/health`, { headers: HEADERS, cache: 'no-store' })
  if (!res.ok) throw new Error('Health check failed')
  return res.json()
}

// ── Formatters ────────────────────────────────────────────────────────

export function fmt_ms(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function fmt_cost(usd: number): string {
  if (usd === 0) return '$0.00'
  if (usd < 0.001) return `$${(usd * 1000000).toFixed(2)}µ`
  if (usd < 0.01)  return `$${(usd * 1000).toFixed(3)}m`
  return `$${usd.toFixed(4)}`
}

export function fmt_pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

export function fmt_time(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function short_id(id: string): string {
  return id.slice(0, 8).toUpperCase()
}