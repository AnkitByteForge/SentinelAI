'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts'

// ── Constants ─────────────────────────────────────────────────────────
const BASE    = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
const HEADERS = { 'Authorization': 'Bearer sentinel-dev-key-123', 'Content-Type': 'application/json' }

const C = {
  cyan:    '#00E5CC',
  amber:   '#F59E0B',
  emerald: '#10B981',
  rose:    '#F43F5E',
  violet:  '#A78BFA',
  blue:    '#60A5FA',
  dim:     '#4A5568',
  border:  '#1A2332',
  surface: '#0D1117',
  text:    '#CBD5E1',
  textHi:  '#E2E8F0',
}

// ── Types ─────────────────────────────────────────────────────────────
interface Metrics {
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

interface LogEntry {
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

interface CacheStats {
  total_entries: number
  total_hits: number
  total_saved_usd: number
  threshold: number
  top_cached: Array<{ prompt_preview: string; hit_count: number; saved_usd: number }>
}

interface CircuitState {
  state: 'closed' | 'open' | 'half_open'
  failure_count: number
  last_failure: number
}

// ── Formatters ────────────────────────────────────────────────────────
function fmtMs(ms: number): string {
  if (ms === 0) return '0ms'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtCost(usd: number): string {
  if (usd === 0) return '$0.00'
  if (usd < 0.0001) return `$${(usd * 1000000).toFixed(1)}µ`
  if (usd < 0.01)   return `$${usd.toFixed(6)}`
  return `$${usd.toFixed(4)}`
}

function fmtPct(r: number): string {
  return `${(r * 100).toFixed(1)}%`
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('en-US', { hour12: false })
  } catch {
    return iso
  }
}

function shortId(id: string): string {
  return id.slice(0, 8).toUpperCase()
}

// ── API calls ─────────────────────────────────────────────────────────
async function getMetrics(win: string): Promise<Metrics | null> {
  try {
    const r = await fetch(`${BASE}/v1/metrics?window=${win}`, { headers: HEADERS })
    if (!r.ok) return null
    return r.json()
  } catch { return null }
}

async function getLogs(limit = 100): Promise<LogEntry[]> {
  try {
    const r = await fetch(`${BASE}/v1/logs?limit=${limit}`, { headers: HEADERS })
    if (!r.ok) return []
    const d = await r.json()
    return d.logs || []
  } catch { return [] }
}

async function getCacheStats(): Promise<CacheStats | null> {
  try {
    const r = await fetch(`${BASE}/v1/cache/stats`, { headers: HEADERS })
    if (!r.ok) return null
    return r.json()
  } catch { return null }
}

async function getCircuits(): Promise<Record<string, CircuitState>> {
  try {
    const r = await fetch(`${BASE}/v1/circuit/states`, { headers: HEADERS })
    if (!r.ok) return {}
    return r.json()
  } catch { return {} }
}

// ── Small UI pieces ───────────────────────────────────────────────────
function Pill({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: 'inline-block',
      fontSize: 9, fontWeight: 700, padding: '2px 7px',
      borderRadius: 4, letterSpacing: '0.07em', textTransform: 'uppercase',
      background: `${color}18`, color, border: `1px solid ${color}30`,
    }}>
      {label}
    </span>
  )
}

function Dot({ color }: { color: string }) {
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
      background: color, boxShadow: `0 0 6px ${color}`, flexShrink: 0,
    }} />
  )
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: C.surface,
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      padding: '20px 22px',
      position: 'relative',
      overflow: 'hidden',
      ...style,
    }}>
      {children}
    </div>
  )
}

function SecTitle({ label, sub }: { label: string; sub?: string }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        fontFamily: 'Syne, sans-serif', fontWeight: 700, fontSize: 12,
        letterSpacing: '0.1em', textTransform: 'uppercase', color: C.textHi,
      }}>
        {label}
      </div>
      {sub && <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function ChartTip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: '#0A0F16', border: `1px solid ${C.border}`,
      borderRadius: 8, padding: '8px 12px', fontSize: 11,
    }}>
      {label && <div style={{ color: C.dim, marginBottom: 4 }}>{label}</div>}
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ color: p.color || C.text }}>
          {p.name}: <strong style={{ color: C.textHi }}>{p.value}</strong>
        </div>
      ))}
    </div>
  )
}

// ── Metric cards ──────────────────────────────────────────────────────
function MetricCard({
  label, value, sub, accent,
}: { label: string; value: string; sub?: string; accent: string }) {
  return (
    <Card>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg, transparent, ${accent}60, transparent)`,
      }} />
      <div style={{
        fontFamily: 'Syne, sans-serif', fontSize: 30, fontWeight: 800,
        color: C.textHi, letterSpacing: '-0.03em', lineHeight: 1,
      }}>
        {value}
      </div>
      <div style={{
        fontSize: 10, fontWeight: 600, color: C.dim,
        marginTop: 8, letterSpacing: '0.1em', textTransform: 'uppercase',
      }}>
        {label}
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: `${accent}CC`, marginTop: 4 }}>{sub}</div>
      )}
    </Card>
  )
}

// ── Request pipeline flow ─────────────────────────────────────────────
function PipelineFlow({ log }: { log: LogEntry | null }) {
  if (!log) {
    return (
      <Card>
        <SecTitle label="Request Pipeline" sub="Live flow of last request" />
        <div style={{ textAlign: 'center', color: C.dim, fontSize: 12, padding: '24px 0' }}>
          No requests yet — fire a request to see the pipeline
        </div>
      </Card>
    )
  }

  const hit      = log.cache_hit
  const fallback = !!log.fallback_from
  const failed   = log.status === 'error'

  const nodes = [
    { label: 'REQUEST',   detail: '',                                        active: true,  color: C.cyan    },
    { label: 'CACHE',     detail: hit ? 'HIT ✓' : 'MISS ✗',                 active: true,  color: hit ? C.emerald : C.amber },
    { label: 'LLM CALL',  detail: hit ? 'SKIPPED' : log.provider?.toUpperCase(), active: !hit, color: hit ? C.dim : (failed ? C.rose : C.violet) },
    { label: 'RESPONSE',  detail: failed ? 'ERROR' : `${fmtMs(log.latency_ms)}`, active: true, color: failed ? C.rose : C.emerald },
  ]

  return (
    <Card>
      <SecTitle label="Request Pipeline" sub="Live flow of last request" />

      {/* Flow nodes */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 20 }}>
        {nodes.map((n, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
            <div style={{
              flex: 1, border: `1px solid ${n.active ? n.color : C.border}`,
              borderRadius: 8, padding: '10px 6px', textAlign: 'center',
              background: n.active ? `${n.color}0C` : 'transparent',
              opacity: n.active ? 1 : 0.35, transition: 'all 0.3s',
            }}>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', color: n.color }}>{n.label}</div>
              {n.detail && <div style={{ fontSize: 9, color: n.color, marginTop: 3, opacity: 0.8 }}>{n.detail}</div>}
            </div>
            {i < nodes.length - 1 && (
              <div style={{ width: 20, textAlign: 'center', color: C.cyan, opacity: 0.4, fontSize: 14, flexShrink: 0 }}>›</div>
            )}
          </div>
        ))}
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
        {[
          { k: 'LATENCY',  v: fmtMs(log.latency_ms), c: log.latency_ms < 200 ? C.emerald : log.latency_ms < 3000 ? C.amber : C.rose },
          { k: 'PROVIDER', v: (log.provider || '—').toUpperCase(), c: log.provider === 'groq' ? C.violet : C.blue },
          { k: 'CACHE',    v: log.cache_hit ? 'HIT' : 'MISS',   c: log.cache_hit ? C.emerald : C.amber },
          { k: 'COST',     v: log.cost_usd === 0 ? 'FREE' : fmtCost(log.cost_usd), c: C.cyan },
        ].map(item => (
          <div key={item.k} style={{
            background: `${item.c}08`, border: `1px solid ${item.c}22`,
            borderRadius: 7, padding: '7px 6px', textAlign: 'center',
          }}>
            <div style={{ fontSize: 8, color: C.dim, letterSpacing: '0.08em', marginBottom: 3 }}>{item.k}</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: item.c, fontFamily: 'Syne, sans-serif' }}>{item.v}</div>
          </div>
        ))}
      </div>

      {fallback && (
        <div style={{
          marginTop: 10, padding: '8px 12px', borderRadius: 8,
          background: `${C.amber}08`, border: `1px solid ${C.amber}25`,
          fontSize: 11, color: C.amber, display: 'flex', alignItems: 'center', gap: 6,
        }}>
          ⚡ FAILOVER — {log.fallback_from?.toUpperCase()} failed → {log.provider?.toUpperCase()} responded
        </div>
      )}
    </Card>
  )
}

// ── Circuit breakers ──────────────────────────────────────────────────
function CircuitPanel({ circuits }: { circuits: Record<string, CircuitState> }) {
  const entries = Object.entries(circuits)
  const anyOpen = entries.some(([, c]) => c.state !== 'closed')

  const stateColor = (s: string) => s === 'closed' ? C.emerald : s === 'open' ? C.rose : C.amber
  const stateLabel = (s: string) => s === 'closed' ? 'HEALTHY' : s === 'open' ? 'OPEN' : 'TESTING'

  return (
    <Card>
      <SecTitle label="Circuit Breakers" sub="Provider health & failover state" />

      {entries.length === 0 ? (
        <div style={{ textAlign: 'center', color: C.dim, fontSize: 11, padding: '12px 0' }}>
          No circuit data yet
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {entries.map(([name, c]) => {
            const col = stateColor(c.state)
            return (
              <div key={name} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '10px 12px', borderRadius: 8,
                background: `${col}08`, border: `1px solid ${col}25`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: col, boxShadow: `0 0 8px ${col}`,
                  }} />
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: C.textHi, textTransform: 'uppercase' }}>
                      {name}
                    </div>
                    <div style={{ fontSize: 10, color: C.dim }}>
                      {c.failure_count} / 3 failures
                    </div>
                  </div>
                </div>
                <Pill label={stateLabel(c.state)} color={col} />
              </div>
            )
          })}
        </div>
      )}

      <div style={{
        marginTop: 12, padding: '8px 12px', borderRadius: 8,
        background: anyOpen ? `${C.rose}08` : `${C.emerald}08`,
        border: `1px solid ${anyOpen ? C.rose : C.emerald}25`,
        fontSize: 11, color: anyOpen ? C.rose : C.emerald,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {anyOpen ? '⚠ Degraded — fallback routing active' : '✓ All providers operational'}
      </div>
    </Card>
  )
}

// ── Latency chart ─────────────────────────────────────────────────────
function LatencyChart({ logs }: { logs: LogEntry[] }) {
  const data = [...logs].reverse().slice(-30).map((l, i) => ({
    n:       i + 1,
    latency: l.latency_ms,
    hit:     l.cache_hit,
  }))

  return (
    <Card>
      <SecTitle label="Latency Timeline" sub="Last 30 requests — green = cache hit, purple = LLM call" />
      <div style={{ height: 180 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
            <defs>
              <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={C.cyan} stopOpacity={0.15} />
                <stop offset="95%" stopColor={C.cyan} stopOpacity={0}    />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis dataKey="n" tick={{ fontSize: 10, fill: C.dim }} />
            <YAxis tick={{ fontSize: 10, fill: C.dim }} tickFormatter={(v: number) => `${v}ms`} />
            <Tooltip content={<ChartTip />} formatter={(v: any) => [`${v}ms`, 'Latency']} />
            <Area
              type="monotone" dataKey="latency" name="Latency"
              stroke={C.cyan} strokeWidth={2} fill="url(#areaGrad)"
              dot={(props: any) => {
                const { cx, cy, payload } = props
                const col = payload.hit ? C.emerald : C.violet
                return <circle key={`dot-${cx}`} cx={cx} cy={cy} r={4} fill={col} stroke={C.surface} strokeWidth={1} />
              }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div style={{ display: 'flex', gap: 16, marginTop: 10, fontSize: 11, color: C.dim }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><Dot color={C.emerald} /> Cache hit (~15ms)</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><Dot color={C.violet}  /> LLM call (~2000ms)</span>
      </div>
    </Card>
  )
}

// ── Cache analysis ────────────────────────────────────────────────────
function CachePanel({ metrics, cache }: { metrics: Metrics | null; cache: CacheStats | null }) {
  const hits   = metrics ? Math.round(metrics.cache_hit_rate * metrics.total_requests) : 0
  const misses = metrics ? metrics.total_requests - hits : 0
  const pie    = [
    { name: 'Hit',  value: hits,   color: C.emerald },
    { name: 'Miss', value: misses, color: C.amber   },
  ]

  return (
    <Card>
      <SecTitle label="Cache Analysis" sub="Hit/miss ratio and cost saved" />
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ width: 130, height: 130, flexShrink: 0 }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={pie} cx="50%" cy="50%" innerRadius={38} outerRadius={58} dataKey="value" strokeWidth={0}>
                {pie.map((d, i) => <Cell key={i} fill={d.color} opacity={0.85} />)}
              </Pie>
              <Tooltip content={<ChartTip />} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div style={{ flex: 1 }}>
          {pie.map(d => (
            <div key={d.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: C.text }}>
                <Dot color={d.color} /> {d.name}
              </span>
              <span style={{ fontSize: 16, fontWeight: 700, color: d.color, fontFamily: 'Syne, sans-serif' }}>
                {d.value}
              </span>
            </div>
          ))}
          <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 10 }}>
            <div style={{ fontSize: 10, color: C.dim, marginBottom: 2, letterSpacing: '0.06em' }}>COST SAVED</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: C.emerald, fontFamily: 'Syne, sans-serif' }}>
              {fmtCost(cache?.total_saved_usd || 0)}
            </div>
          </div>
        </div>
      </div>

      {cache && cache.top_cached.length > 0 && (
        <div style={{ marginTop: 14, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
          <div style={{ fontSize: 10, color: C.dim, letterSpacing: '0.06em', marginBottom: 8 }}>TOP CACHED PROMPTS</div>
          {cache.top_cached.slice(0, 3).map((t, i) => (
            <div key={i} style={{ marginBottom: 6, padding: '7px 10px', borderRadius: 7, background: '#FFFFFF04' }}>
              <div style={{ fontSize: 11, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {t.prompt_preview}
              </div>
              <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>
                {t.hit_count} hits · saved {fmtCost(t.saved_usd)}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

// ── Provider chart ────────────────────────────────────────────────────
function ProviderChart({ metrics }: { metrics: Metrics | null }) {
  const data = metrics
    ? Object.entries(metrics.providers).map(([name, s]) => ({
        name:     name.charAt(0).toUpperCase() + name.slice(1),
        requests: s.requests,
        errors:   s.errors,
        latency:  Math.round(s.avg_latency_ms),
        color:    name === 'groq' ? C.violet : C.blue,
      }))
    : []

  return (
    <Card>
      <SecTitle label="Provider Usage" sub="Requests per provider with error overlay" />
      {data.length > 0 ? (
        <>
          <div style={{ height: 160 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                <XAxis dataKey="name" tick={{ fontSize: 10, fill: C.dim }} />
                <YAxis tick={{ fontSize: 10, fill: C.dim }} />
                <Tooltip content={<ChartTip />} />
                <Bar dataKey="requests" name="Requests" radius={[4, 4, 0, 0]}>
                  {data.map((d, i) => <Cell key={i} fill={d.color} opacity={0.8} />)}
                </Bar>
                <Bar dataKey="errors" name="Errors" radius={[4, 4, 0, 0]} fill={C.rose} opacity={0.7} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8, marginTop: 12 }}>
            {data.map(d => (
              <div key={d.name} style={{
                padding: '8px 10px', borderRadius: 8,
                background: `${d.color}08`, border: `1px solid ${d.color}25`,
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: C.textHi, marginBottom: 3 }}>{d.name}</div>
                <div style={{ fontSize: 10, color: C.dim }}>{d.requests} req · {d.latency}ms avg</div>
                {d.errors > 0 && <div style={{ fontSize: 10, color: C.rose, marginTop: 2 }}>{d.errors} errors</div>}
              </div>
            ))}
          </div>
        </>
      ) : (
        <div style={{ height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.dim, fontSize: 12 }}>
          No provider data in window
        </div>
      )}
    </Card>
  )
}

// ── Log detail modal ──────────────────────────────────────────────────
function LogModal({ log, onClose }: { log: LogEntry; onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  const fields = [
    { k: 'Provider',     v: log.provider?.toUpperCase() || '—',                    c: log.provider === 'groq' ? C.violet : C.blue    },
    { k: 'Model',        v: log.model || '—',                                       c: C.text                                          },
    { k: 'Status',       v: log.status?.toUpperCase() || '—',                       c: log.status === 'success' ? C.emerald : C.rose   },
    { k: 'Cache',        v: log.cache_hit ? 'HIT ✓' : 'MISS ✗',                   c: log.cache_hit ? C.emerald : C.amber             },
    { k: 'Latency',      v: fmtMs(log.latency_ms),                                  c: C.text                                          },
    { k: 'Cost',         v: log.cost_usd === 0 ? 'FREE (cached)' : fmtCost(log.cost_usd), c: log.cost_usd === 0 ? C.emerald : C.text  },
    { k: 'Input tokens', v: String(log.input_tokens),                                c: C.text                                          },
    { k: 'Output tokens',v: String(log.output_tokens),                               c: C.text                                          },
  ]

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#0A0F16', border: `1px solid ${C.border}`,
          borderRadius: 16, padding: 28, width: 500, maxWidth: '90vw',
          maxHeight: '80vh', overflow: 'auto',
          boxShadow: `0 0 60px rgba(0,229,204,0.12)`,
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 10, color: C.dim, letterSpacing: '0.08em', marginBottom: 4 }}>REQUEST DETAIL</div>
            <div style={{ fontFamily: 'Syne, sans-serif', fontSize: 14, fontWeight: 700, color: C.cyan, wordBreak: 'break-all' }}>
              {log.id}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: C.dim, cursor: 'pointer', fontSize: 20, lineHeight: 1, padding: '0 0 0 16px' }}
          >
            ×
          </button>
        </div>

        {/* Fields grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {fields.map(f => (
            <div key={f.k} style={{
              padding: '10px 12px', borderRadius: 8,
              background: '#FFFFFF04', border: `1px solid ${C.border}`,
            }}>
              <div style={{ fontSize: 9, color: C.dim, letterSpacing: '0.08em', marginBottom: 3 }}>{f.k.toUpperCase()}</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: f.c }}>{f.v}</div>
            </div>
          ))}
        </div>

        {/* Failover banner */}
        {log.fallback_from && (
          <div style={{
            marginTop: 12, padding: '10px 12px', borderRadius: 8,
            background: `${C.amber}08`, border: `1px solid ${C.amber}25`,
          }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: C.amber, marginBottom: 3 }}>⚡ FAILOVER EVENT</div>
            <div style={{ fontSize: 11, color: C.text }}>
              Primary <strong style={{ color: C.rose }}>{log.fallback_from}</strong> failed →
              routed to <strong style={{ color: C.emerald }}>{log.provider}</strong>
            </div>
          </div>
        )}

        {/* Timestamp */}
        <div style={{
          marginTop: 12, padding: '8px 12px', borderRadius: 8, background: '#FFFFFF04',
          fontSize: 11, color: C.dim, fontFamily: 'JetBrains Mono, monospace',
        }}>
          {new Date(log.created_at).toISOString()}
        </div>
      </div>
    </div>
  )
}

// ── Request logs table ────────────────────────────────────────────────
function LogsTable({ logs, onSelect }: { logs: LogEntry[]; onSelect: (l: LogEntry) => void }) {
  const [statusF,   setStatusF]   = useState('')
  const [providerF, setProviderF] = useState('')
  const [sortKey,   setSortKey]   = useState<'latency_ms' | 'cost_usd' | 'created_at'>('created_at')
  const [sortDir,   setSortDir]   = useState<'asc' | 'desc'>('desc')

  const filtered = logs
    .filter(l => !statusF   || l.status   === statusF)
    .filter(l => !providerF || l.provider === providerF)
    .sort((a, b) => {
      const va = a[sortKey] as any, vb = b[sortKey] as any
      return sortDir === 'desc' ? (vb > va ? 1 : -1) : (va > vb ? 1 : -1)
    })

  const toggleSort = (k: typeof sortKey) => {
    if (sortKey === k) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(k); setSortDir('desc') }
  }

  const sel = (field: typeof sortKey) => ({
    cursor: 'pointer' as const,
    color: sortKey === field ? C.cyan : C.dim,
    userSelect: 'none' as const,
  })

  return (
    <Card>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
        <SecTitle label="Request Logs" sub={`${filtered.length} of ${logs.length} entries`} />
        <div style={{ display: 'flex', gap: 8 }}>
          <select
            value={statusF}
            onChange={e => setStatusF(e.target.value)}
            style={{
              background: C.surface, border: `1px solid ${C.border}`,
              borderRadius: 6, padding: '4px 8px', fontSize: 11, color: C.text, outline: 'none',
            }}
          >
            <option value="">All status</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
            <option value="fallback">Fallback</option>
          </select>
          <select
            value={providerF}
            onChange={e => setProviderF(e.target.value)}
            style={{
              background: C.surface, border: `1px solid ${C.border}`,
              borderRadius: 6, padding: '4px 8px', fontSize: 11, color: C.text, outline: 'none',
            }}
          >
            <option value="">All providers</option>
            <option value="groq">Groq</option>
            <option value="gemini">Gemini</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {[
                { label: 'ID',       key: null            },
                { label: 'Provider', key: null            },
                { label: 'Latency',  key: 'latency_ms'   },
                { label: 'Cache',    key: null            },
                { label: 'Status',   key: null            },
                { label: 'Cost',     key: 'cost_usd'     },
                { label: 'Time',     key: 'created_at'   },
              ].map(col => (
                <th
                  key={col.label}
                  onClick={() => col.key && toggleSort(col.key as any)}
                  style={{
                    padding: '8px 12px', fontSize: 9, fontWeight: 700,
                    letterSpacing: '0.08em', textAlign: 'left', textTransform: 'uppercase',
                    whiteSpace: 'nowrap',
                    ...(col.key ? sel(col.key as any) : { color: C.dim }),
                  }}
                >
                  {col.label}
                  {col.key && sortKey === col.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                </th>
              ))}
              <th style={{ padding: '8px 12px', width: 24 }} />
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 50).map(log => (
              <tr
                key={log.id}
                onClick={() => onSelect(log)}
                style={{ borderBottom: `1px solid ${C.border}22`, cursor: 'pointer', transition: 'background 0.12s' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,229,204,0.03)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                <td style={{ padding: '10px 12px', fontSize: 11, color: C.cyan, fontFamily: 'JetBrains Mono, monospace' }}>
                  {shortId(log.id)}
                </td>
                <td style={{ padding: '10px 12px' }}>
                  <Pill label={log.provider || '?'} color={log.provider === 'groq' ? C.violet : C.blue} />
                </td>
                <td style={{ padding: '10px 12px', fontSize: 12, fontWeight: 600, color: log.latency_ms < 200 ? C.emerald : log.latency_ms < 3000 ? C.text : C.amber }}>
                  {fmtMs(log.latency_ms)}
                </td>
                <td style={{ padding: '10px 12px' }}>
                  <Pill label={log.cache_hit ? 'HIT' : 'MISS'} color={log.cache_hit ? C.emerald : C.amber} />
                </td>
                <td style={{ padding: '10px 12px' }}>
                  <Pill
                    label={log.fallback_from ? 'FALLBACK' : log.status}
                    color={log.status === 'success' ? C.emerald : log.status === 'fallback' ? C.amber : C.rose}
                  />
                </td>
                <td style={{ padding: '10px 12px', fontSize: 11, color: log.cost_usd === 0 ? C.emerald : C.text }}>
                  {log.cost_usd === 0 ? 'FREE' : fmtCost(log.cost_usd)}
                </td>
                <td style={{ padding: '10px 12px', fontSize: 10, color: C.dim }}>
                  {fmtTime(log.created_at)}
                </td>
                <td style={{ padding: '10px 12px', fontSize: 12, color: C.dim }}>›</td>
              </tr>
            ))}
          </tbody>
        </table>

        {filtered.length === 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: C.dim, fontSize: 12 }}>
            No logs match filters
          </div>
        )}
      </div>
    </Card>
  )
}

// ── Main page ─────────────────────────────────────────────────────────
export default function Dashboard() {
  const [metrics,    setMetrics]    = useState<Metrics | null>(null)
  const [logs,       setLogs]       = useState<LogEntry[]>([])
  const [cache,      setCache]      = useState<CacheStats | null>(null)
  const [circuits,   setCircuits]   = useState<Record<string, CircuitState>>({})
  const [win,        setWin]        = useState('24h')
  const [loading,    setLoading]    = useState(true)
  const [lastUpdate, setLastUpdate] = useState('')
  const [selLog,     setSelLog]     = useState<LogEntry | null>(null)

  const refresh = useCallback(async () => {
    const [m, l, cs, ci] = await Promise.all([
      getMetrics(win), getLogs(100), getCacheStats(), getCircuits(),
    ])
    if (m)  setMetrics(m)
    if (l)  setLogs(l)
    if (cs) setCache(cs)
    setCircuits(ci)
    setLastUpdate(new Date().toLocaleTimeString())
    setLoading(false)
  }, [win])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const t = setInterval(refresh, 15000)
    return () => clearInterval(t)
  }, [refresh])

  const anyOpen = Object.values(circuits).some(c => c.state !== 'closed')

  return (
    <>
      <div style={{ minHeight: '100vh', padding: '20px 24px 48px', fontFamily: 'JetBrains Mono, monospace' }}>

        {/* ── Header ── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 24, paddingBottom: 20, borderBottom: `1px solid ${C.border}`,
          flexWrap: 'wrap', gap: 12,
        }}>
          {/* Logo */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{
              width: 42, height: 42, borderRadius: 10,
              background: `linear-gradient(135deg, ${C.cyan}22, ${C.cyan}06)`,
              border: `1px solid ${C.cyan}40`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 20,
            }}>
              ⬡
            </div>
            <div>
              <div style={{
                fontFamily: 'Syne, sans-serif', fontSize: 22, fontWeight: 800,
                color: '#E2E8F0', letterSpacing: '-0.02em',
              }}>
                SENTINEL<span style={{ color: C.cyan }}>AI</span>
              </div>
              <div style={{ fontSize: 9, color: C.dim, letterSpacing: '0.14em', marginTop: 1 }}>
                LLM GATEWAY OBSERVATORY
              </div>
            </div>
          </div>

          {/* Right controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            {/* System status */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 7,
              padding: '6px 12px', borderRadius: 8, fontSize: 11,
              background: anyOpen ? `${C.rose}08`    : `${C.emerald}08`,
              border:     `1px solid ${anyOpen ? C.rose : C.emerald}30`,
              color:       anyOpen ? C.rose : C.emerald,
            }}>
              <span style={{
                display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
                background: anyOpen ? C.rose : C.emerald,
                boxShadow: `0 0 6px ${anyOpen ? C.rose : C.emerald}`,
              }} />
              {anyOpen ? 'DEGRADED' : 'OPERATIONAL'}
            </div>

            {/* Time window */}
            <div style={{ display: 'flex', gap: 4 }}>
              {['1h', '6h', '24h', '7d'].map(w => (
                <button
                  key={w}
                  onClick={() => setWin(w)}
                  style={{
                    padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                    cursor: 'pointer', transition: 'all 0.15s',
                    background: win === w ? `${C.cyan}18` : 'transparent',
                    border:     `1px solid ${win === w ? C.cyan : C.border}`,
                    color:       win === w ? C.cyan : C.dim,
                    fontFamily: 'JetBrains Mono, monospace',
                  }}
                >
                  {w}
                </button>
              ))}
            </div>

            {/* Refresh */}
            <button
              onClick={refresh}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '5px 12px', borderRadius: 6, fontSize: 11,
                background: 'transparent', border: `1px solid ${C.border}`,
                color: C.dim, cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              ↻ {lastUpdate || 'Loading...'}
            </button>
          </div>
        </div>

        {/* ── Metric cards ── */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          <MetricCard
            label="Total Requests" accent={C.cyan}
            value={String(metrics?.total_requests ?? 0)}
            sub={`${metrics?.successful_requests ?? 0} successful`}
          />
          <MetricCard
            label="Cache Hit Rate" accent={C.emerald}
            value={fmtPct(metrics?.cache_hit_rate ?? 0)}
            sub={`${fmtCost(cache?.total_saved_usd ?? 0)} saved`}
          />
          <MetricCard
            label="Avg Latency" accent={C.amber}
            value={fmtMs(Math.round(metrics?.latency.avg_ms ?? 0))}
            sub={`p95: ${fmtMs(metrics?.latency.p95_ms ?? 0)}`}
          />
          <MetricCard
            label="Est. API Cost" accent={C.violet}
            value={fmtCost(metrics?.total_cost_usd ?? 0)}
            sub={`window: ${win}`}
          />
        </div>

        {/* ── Pipeline + Circuits ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 12, marginBottom: 12 }}>
          <PipelineFlow log={logs[0] || null} />
          <CircuitPanel circuits={circuits} />
        </div>

        {/* ── Latency + Cache ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 12, marginBottom: 12 }}>
          <LatencyChart logs={logs} />
          <CachePanel   metrics={metrics} cache={cache} />
        </div>

        {/* ── Provider chart ── */}
        <div style={{ marginBottom: 12 }}>
          <ProviderChart metrics={metrics} />
        </div>

        {/* ── Logs table ── */}
        <LogsTable logs={logs} onSelect={setSelLog} />

        {/* ── Log detail modal ── */}
        {selLog && <LogModal log={selLog} onClose={() => setSelLog(null)} />}

        {/* ── Footer ── */}
        <div style={{ marginTop: 24, textAlign: 'center', fontSize: 10, color: C.dim, letterSpacing: '0.08em' }}>
          SENTINELAI GATEWAY · AUTO-REFRESH 15s · {lastUpdate || '—'}
        </div>
      </div>
    </>
  )
}