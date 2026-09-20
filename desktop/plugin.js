/**
 * SMF Overwatch pane — Omarchy Overwatch live-layer status in a right-of-chat column.
 * Disk plugin: jsx/jsxs only. Never invent layer points or case notes.
 */
import {
  Badge,
  Button,
  Codicon,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  ScrollArea,
  Separator,
  StatusDot,
  cn,
  fmtDateTime,
  haptic,
  host,
  PALETTE_AREA,
  PANES_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  relativeTime,
  useQuery,
  useQueryClient,
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'smf-overwatch-pane'
const ROUTE = '/overwatch'
const POLL_MS = 2 * 60 * 1000
const HUD_PREVIEW = 'http://127.0.0.1:4173/'
const HUD_DEV = 'http://127.0.0.1:5173/'
const GITHUB_URL = 'https://github.com/smfworks/omarchy-overwatch'
const CASES_KEY = 'omarchy-overwatch.cases.v1'

const HUD = {
  cyan: '#3ee0c8',
  amber: '#e8b84a',
  red: '#ff5d6c',
  muted: '#7c8ea3',
  text: '#d7e4f2',
}

const LAYER_ORDER = ['earthquakes', 'eonet', 'opensky', 'nws', 'ais', 'firms']

function emptyLayers() {
  return {
    ok: true,
    stale: false,
    read_status: 'ok',
    empty: true,
    layers: [],
    summary: { live: 0, stale: 0, err: 0, off: 0, total: 0 },
    hud: {
      preview_url: HUD_PREVIEW,
      dev_url: HUD_DEV,
      github_url: GITHUB_URL,
      reachable: null,
      reachable_url: null,
      cases_key: CASES_KEY,
      cases_note:
        'Cases live in the Overwatch HUD browser localStorage key omarchy-overwatch.cases.v1. This pane does not read or sync them.',
    },
    errors: [],
    warnings: [],
    fetched_at: null,
  }
}

function asLayers(data) {
  if (!data || !Array.isArray(data.layers)) return []
  return data.layers.filter((row) => row && typeof row === 'object' && row.id && row.label)
}

function hasReadProblems(data) {
  if (!data) return false
  if (data.ok === false) return true
  if (data.read_status === 'unread') return true
  const errs = data.errors
  return Array.isArray(errs) && errs.length > 0 && asLayers(data).length === 0
}

function isUnread(data) {
  if (!data) return false
  return (data.ok === false || data.read_status === 'unread') && asLayers(data).length === 0
}

function formatErrors(data) {
  const errs = (data && data.errors) || []
  const lines = errs
    .map((e) => {
      if (!e) return ''
      if (e.kind && e.error) return `${e.kind}: ${e.error}`
      if (e.path) return `${e.path}: ${e.error || e.kind || 'failed'}`
      return e.error || e.kind || ''
    })
    .filter(Boolean)
  if (data && data.error && !lines.length) lines.push(String(data.error))
  if (!lines.length) {
    return 'Could not load Overwatch layers. This is not empty airspace — the feeds were unread. No points were invented.'
  }
  return lines.slice(0, 4).join(' · ')
}

function fmtAge(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return null
  const s = Math.max(0, Math.trunc(Number(seconds)))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

function fmtWhen(value) {
  if (!value) return ''
  try {
    const rel = relativeTime(value)
    if (rel) return rel
  } catch {
    /* formatter optional */
  }
  try {
    return fmtDateTime(value)
  } catch {
    return String(value)
  }
}

function statusColor(status) {
  if (status === 'live') return HUD.cyan
  if (status === 'stale') return HUD.amber
  if (status === 'err') return HUD.red
  return HUD.muted
}

function statusLabel(status) {
  const raw = String(status || 'off').toLowerCase()
  if (raw === 'live') return 'LIVE'
  if (raw === 'stale') return 'STALE'
  if (raw === 'err') return 'ERR'
  return 'OFF'
}

function openExternal(url) {
  if (!url) return
  const tryRequest = (method, params) => {
    try {
      const p = host.request(method, params)
      if (p && typeof p.then === 'function') return p
      return Promise.resolve(p)
    } catch (err) {
      return Promise.reject(err)
    }
  }
  const fallback = () => {
    try {
      if (typeof window !== 'undefined' && typeof window.open === 'function') {
        window.open(url, '_blank', 'noopener,noreferrer')
      }
    } catch {
      /* host blocked popups */
    }
  }
  try {
    if (typeof host.openExternal === 'function') {
      void Promise.resolve(host.openExternal(url)).catch(fallback)
      return
    }
    if (typeof host.open === 'function') {
      void Promise.resolve(host.open(url)).catch(fallback)
      return
    }
  } catch {
    /* continue */
  }
  void tryRequest('os.openExternal', { url })
    .catch(() => tryRequest('os.open', { url }))
    .catch(() => tryRequest('shell.openExternal', { url }))
    .catch(fallback)
}

async function fetchLayers(ctx, refresh) {
  const path = refresh ? '/layers?refresh=1' : '/layers'
  const data = await ctx.rest(path)
  if (!data || typeof data !== 'object') {
    throw new Error('Layers response was empty')
  }
  return data
}

function HudDot({ status }) {
  const color = statusColor(status)
  const glow = status === 'live' || status === 'err'
  return jsx('span', {
    'aria-hidden': true,
    style: {
      width: 7,
      height: 7,
      borderRadius: 99,
      background: color,
      boxShadow: glow ? `0 0 8px ${color}` : 'none',
      flexShrink: 0,
    },
  })
}

function LayerRow({ layer }) {
  const status = String(layer.status || 'off').toLowerCase()
  const age = fmtAge(layer.cache_age_seconds)
  const samples = Array.isArray(layer.sample) ? layer.sample.filter((p) => p && p.label) : []
  const color = statusColor(status)
  return jsxs('div', {
    className: cn(
      'flex flex-col gap-1.5 rounded-xl border px-3 py-2.5',
      'border-(--ui-stroke-secondary)'
    ),
    style: {
      borderColor: status === 'live' ? 'rgba(62, 224, 200, 0.28)' : undefined,
    },
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(HudDot, { status }),
          jsx('div', {
            className: 'min-w-0 flex-1 truncate text-sm font-medium tracking-wide',
            children: layer.label,
          }),
          jsx(Badge, {
            className: 'shrink-0 text-[0.625rem]',
            children: statusLabel(status),
          }),
        ],
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          status === 'off'
            ? jsx('span', { children: 'not fetched' })
            : jsx('span', {
                children:
                  typeof layer.count === 'number'
                    ? `${layer.count} sampled`
                    : 'count unknown',
              }),
          layer.updated_at
            ? jsx('span', { children: fmtWhen(layer.updated_at) })
            : null,
          layer.from_cache && age
            ? jsx('span', { children: `cache ${age}` })
            : null,
        ],
      }),
      layer.note
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
            children: layer.note,
          })
        : null,
      layer.error
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-secondary)',
            style: { color: status === 'err' ? HUD.red : HUD.amber },
            children: String(layer.error),
          })
        : null,
      samples.length
        ? jsx('div', {
            className: 'flex flex-col gap-0.5 pt-0.5',
            children: samples.slice(0, 3).map((pt) =>
              jsxs(
                'div',
                {
                  className: 'truncate text-[0.6875rem] text-(--ui-text-secondary)',
                  style: { color: HUD.text },
                  children: [
                    jsx('span', { style: { color }, children: '▸ ' }),
                    pt.label,
                  ],
                },
                pt.id || pt.label
              )
            ),
          })
        : status === 'live' && layer.count === 0
          ? jsx('div', {
              className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
              children: 'Source returned zero points. Empty, not invented.',
            })
          : null,
    ],
  })
}

function HudHeader({ data, isFetching, onRefresh }) {
  const hud = (data && data.hud) || emptyLayers().hud
  const summary = (data && data.summary) || emptyLayers().summary
  const reachable = hud.reachable
  return jsxs('div', {
    className: 'flex flex-col gap-2 px-4 pt-4',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Codicon, { name: 'globe', size: 16 }),
          jsx('div', {
            className: 'min-w-0 flex-1 truncate text-sm font-medium tracking-[0.12em]',
            style: { color: HUD.cyan },
            children: 'OVERWATCH',
          }),
          reachable === true
            ? jsx(Badge, { className: 'text-[0.625rem]', children: 'HUD up' })
            : reachable === false
              ? jsx(Badge, { className: 'text-[0.625rem]', children: 'HUD down' })
              : null,
          isFetching
            ? jsx('span', { className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: 'updating' })
            : null,
          jsx(Button, {
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              haptic('tap')
              onRefresh()
            },
            children: 'Refresh',
          }),
        ],
      }),
      jsx('div', {
        className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
        children:
          'Live public-feed status for the Omarchy Overwatch HUD. Complements the globe — it does not replace it, and it does not invent tracks.',
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'live' }), `${summary.live || 0} LIVE`] }),
          jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'stale' }), `${summary.stale || 0} STALE`] }),
          jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'err' }), `${summary.err || 0} ERR`] }),
          jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'off' }), `${summary.off || 0} OFF`] }),
        ],
      }),
    ],
  })
}

function Actions({ hud }) {
  const preview = (hud && hud.preview_url) || HUD_PREVIEW
  const github = (hud && hud.github_url) || GITHUB_URL
  const reachableUrl = hud && hud.reachable_url
  return jsxs('div', {
    className: 'flex flex-col gap-2 px-4',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2',
        children: [
          jsx(Button, {
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              haptic('tap')
              openExternal(reachableUrl || preview)
            },
            children: jsxs('span', {
              className: 'inline-flex items-center gap-1.5',
              children: [jsx(Codicon, { name: 'link-external', size: 14 }), 'Open Overwatch HUD'],
            }),
          }),
          jsx(Button, {
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              haptic('tap')
              openExternal(github)
            },
            children: jsxs('span', {
              className: 'inline-flex items-center gap-1.5',
              children: [jsx(Codicon, { name: 'github', size: 14 }), 'GitHub'],
            }),
          }),
        ],
      }),
      jsx('div', {
        className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
        children: `Preview ${preview} · Vite dev ${HUD_DEV}`,
      }),
    ],
  })
}

function CasesTip({ hud }) {
  const note =
    (hud && hud.cases_note) ||
    `Cases live in the Overwatch HUD browser localStorage key ${CASES_KEY}. This pane does not read or sync them.`
  return jsx('div', {
    className: 'mx-4 rounded-md border border-(--ui-stroke-secondary) px-3 py-2 text-[0.6875rem] leading-relaxed text-(--ui-text-secondary)',
    children: note,
  })
}

function OverwatchPane({ ctx }) {
  const queryClient = useQueryClient()
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: [ID, 'layers'],
    queryFn: () => fetchLayers(ctx, false),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
    retry: 1,
  })
  const layers = asLayers(data)
  const unread = Boolean(error && !data) || isUnread(data)
  const ordered = LAYER_ORDER.map((id) => layers.find((row) => row.id === id)).filter(Boolean)
  const extras = layers.filter((row) => !LAYER_ORDER.includes(row.id))
  const visible = ordered.concat(extras)
  const hud = (data && data.hud) || emptyLayers().hud

  if (isLoading) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-center justify-center gap-3',
      children: [
        jsx(GlyphSpinner, { size: 24 }),
        jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'Loading Overwatch layers…' }),
      ],
    })
  }

  if (unread || (hasReadProblems(data) && visible.length === 0)) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-center justify-center gap-3 p-8',
      children: [
        jsx(ErrorState, {
          title: error && !data ? 'Backend not reachable' : 'Could not load Overwatch layers',
          description:
            error && !data
              ? 'Enable Overwatch in Settings → Plugins, then quit Hermes Desktop and relaunch from the menu. Reload desktop plugins is JS only. No layer points were invented.'
              : formatErrors(data),
        }),
        jsx(Button, { variant: 'ghost', size: 'sm', onClick: () => refetch(), children: 'Retry' }),
      ],
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col gap-3',
    children: [
      jsx(HudHeader, {
        data,
        isFetching,
        onRefresh: () => {
          fetchLayers(ctx, true)
            .then((fresh) => {
              queryClient.setQueryData([ID, 'layers'], fresh)
            })
            .catch(() => {
              void refetch()
            })
        },
      }),
      jsx(Actions, { hud }),
      jsx(CasesTip, { hud }),
      jsx(Separator, {}),
      visible.length === 0
        ? jsx('div', {
            className: 'flex flex-1 items-center justify-center p-6',
            children: jsx(EmptyState, {
              title: 'No layer rows',
              description: 'The backend returned zero layers. That is empty, not an invented briefing.',
            }),
          })
        : jsx(ScrollArea, {
            className: 'min-h-0 flex-1',
            children: jsx('div', {
              className: 'flex flex-col gap-2 px-4 pb-6',
              children: visible.map((layer) => jsx(LayerRow, { layer }, layer.id)),
            }),
          }),
    ],
  })
}

function StatusChipHost({ ctx }) {
  const { data } = useQuery({
    queryKey: [ID, 'layers'],
    queryFn: () => fetchLayers(ctx, false),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
    retry: 0,
  })
  if (!data || data.ok === false) return null
  const summary = data.summary || {}
  const err = summary.err || 0
  const stale = summary.stale || 0
  if (!err && !stale) return null
  const tone = err ? 'bad' : 'warn'
  const label = err ? `${err} ERR` : `${stale} STALE`
  return jsx('button', {
    type: 'button',
    className: 'px-1.5 text-[0.6875rem] text-(--ui-text-secondary)',
    onClick: () => {
      haptic('tap')
      host.navigate(ROUTE)
    },
    children: jsxs('span', {
      className: 'inline-flex items-center gap-1',
      children: [
        jsx(StatusDot, { tone }),
        jsx(Codicon, { name: 'globe', size: 12 }),
        label,
      ],
    }),
  })
}

export default {
  id: ID,
  name: 'Overwatch',
  defaultEnabled: true,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Overwatch',
        data: {
          placement: 'right',
          width: '400px',
          dock: { pane: 'workspace', pos: 'right' },
        },
        render: () => jsx(OverwatchPane, { ctx }),
      },
      {
        id: `${ID}-nav`,
        area: SIDEBAR_NAV_AREA,
        data: { path: ROUTE, label: 'Overwatch', codicon: 'globe' },
      },
      {
        id: `${ID}-route`,
        area: ROUTES_AREA,
        data: { path: ROUTE },
        render: () => jsx(OverwatchPane, { ctx }),
      },
      {
        id: `${ID}-palette-hud`,
        area: PALETTE_AREA,
        data: {
          id: `${ID}-open`,
          label: 'Open Overwatch',
          keywords: ['overwatch', 'hud', 'omarchy', 'osint', 'globe'],
          run: () => openExternal(HUD_PREVIEW),
        },
      },
      {
        id: `${ID}-palette-pane`,
        area: PALETTE_AREA,
        data: {
          id: `${ID}-open-pane`,
          label: 'Open Overwatch pane',
          keywords: ['overwatch', 'pane', 'layers', 'usgs', 'eonet', 'ads-b', 'nws', 'ais', 'firms'],
          run: () => host.navigate(ROUTE),
        },
      },
      {
        id: `${ID}-status-chip`,
        area: STATUSBAR_AREAS.right,
        order: 143,
        render: () => jsx(StatusChipHost, { ctx }),
      },
    ])
  },
}
