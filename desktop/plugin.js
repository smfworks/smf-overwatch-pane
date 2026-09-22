/**
 * SMF Overwatch pane — embed the Overwatch HUD beside chat.
 * Disk plugin: jsx/jsxs only. Never invent layer points or case notes.
 * The iframe follows the same local-URL path as AIGC Studio and does not
 * need plugin_api.py. /layers is a secondary strip when that API is mounted.
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
  atom,
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
  useValue,
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'smf-overwatch-pane'
const ROUTE = '/overwatch'
const POLL_MS = 2 * 60 * 1000
const HUD_PREVIEW = 'http://127.0.0.1:4173/'
const HUD_DEV = 'http://127.0.0.1:5173/'
const AIGC_STUDIO = 'http://127.0.0.1:5174/'
const GITHUB_URL = 'https://github.com/smfworks/omarchy-overwatch'
const CASES_KEY = 'omarchy-overwatch.cases.v1'
/** HTML <title> must contain this product string (Overwatch OSINT). */
const HUD_IDENTITY = 'overwatch osint'
/** Same shape as the AIGC Studio pane, plus allow-modals for HUD dialogs. */
const IFRAME_SANDBOX =
  'allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals'
const UNMOUNTED_COPY =
  'Quit Hermes Desktop and relaunch from the menu so plugin_api.py mounts and /layers can load. The HUD iframe does not need that API. Python /layers mounts only for the profile whose HERMES_HOME/profiles/<name>/plugins contains smf-overwatch-pane. ⌘K → Reload desktop plugins is JavaScript only — it does not remount the Python API. No layer points were invented.'

const $iframeFailedUrl = atom('')
const $iframeNonce = atom(0)
const $layersOpen = atom(false)
let iframeNonce = 0

function remountFrame() {
  iframeNonce += 1
  $iframeFailedUrl.set('')
  $iframeNonce.set(iframeNonce)
}

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
      identified: null,
      aigc_studio_url: AIGC_STUDIO,
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

function htmlIdentifiesAsOverwatch(html) {
  const text = String(html || '')
  const match = text.match(/<title[^>]*>([\s\S]*?)<\/title>/i)
  if (match) return match[1].replace(/\s+/g, ' ').toLowerCase().includes(HUD_IDENTITY)
  return text.slice(0, 8192).toLowerCase().includes(HUD_IDENTITY)
}

async function fetchHtml(url) {
  const ctrl = typeof AbortController === 'function' ? new AbortController() : null
  const timer = ctrl ? setTimeout(() => ctrl.abort(), 2000) : null
  try {
    const res = await fetch(url, {
      method: 'GET',
      cache: 'no-store',
      signal: ctrl ? ctrl.signal : undefined,
      headers: { Accept: 'text/html, */*' },
    })
    if (!res || !res.ok) return ''
    return await res.text()
  } catch {
    return ''
  } finally {
    if (timer) clearTimeout(timer)
  }
}

async function probeHudIdentity() {
  for (const url of [HUD_PREVIEW, HUD_DEV]) {
    const html = await fetchHtml(url)
    if (html && htmlIdentifiesAsOverwatch(html)) return url
  }
  return null
}

function verifiedHudUrl(hud) {
  if (!hud || hud.identified !== true) return null
  const url = hud.reachable_url
  if (url === HUD_PREVIEW || url === HUD_DEV) return url
  return null
}

function resolveHud(apiHud, clientUrl, clientSettled) {
  const base = apiHud && typeof apiHud === 'object' ? apiHud : emptyLayers().hud
  const apiUrl = verifiedHudUrl(base)
  if (apiUrl) {
    return Object.assign({}, base, { reachable: true, reachable_url: apiUrl, identified: true })
  }
  if (base.identified === false) {
    return Object.assign({}, base, { reachable: false, reachable_url: null })
  }
  if (clientUrl === HUD_PREVIEW || clientUrl === HUD_DEV) {
    return Object.assign({}, base, { reachable: true, reachable_url: clientUrl, identified: true })
  }
  if (clientSettled) {
    return Object.assign({}, base, { reachable: false, reachable_url: null, identified: false })
  }
  return Object.assign({}, base, { reachable: null, reachable_url: null })
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

function BackendBadge({ unmounted, detail }) {
  const label = unmounted ? 'Backend not reachable' : 'Layers unread'
  const tip = unmounted ? UNMOUNTED_COPY : detail || label
  return jsx('span', {
    title: tip,
    className: 'inline-flex max-w-[16rem]',
    children: jsx(Badge, {
      className: 'truncate text-[0.625rem]',
      children: label,
    }),
  })
}

function SummaryChips({ summary }) {
  if (!summary) return null
  return jsxs('div', {
    className: 'flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)',
    children: [
      jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'live' }), `${summary.live || 0} LIVE`] }),
      jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'stale' }), `${summary.stale || 0} STALE`] }),
      jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'err' }), `${summary.err || 0} ERR`] }),
      jsxs('span', { className: 'inline-flex items-center gap-1', children: [jsx(HudDot, { status: 'off' }), `${summary.off || 0} OFF`] }),
    ],
  })
}

function HudHeader({ data, hud, isFetching, onRefresh }) {
  const resolved = hud || (data && data.hud) || emptyLayers().hud
  const summary = data && data.summary
  const reachable = resolved.reachable
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
          'The pane embeds the Overwatch HUD when :4173 or :5173 titles as Overwatch OSINT. Layer chips are a secondary strip and need plugin_api.py. This is not AIGC Studio. It does not invent tracks.',
      }),
      jsx(SummaryChips, { summary }),
    ],
  })
}

function HudFrame({ url }) {
  const failedUrl = useValue($iframeFailedUrl)
  const nonce = useValue($iframeNonce)
  if (url !== HUD_PREVIEW && url !== HUD_DEV) return null
  if (failedUrl === url) {
    return jsxs('div', {
      className: 'flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6',
      children: [
        jsx(ErrorState, {
          title: 'Could not load the Overwatch HUD',
          description:
            'The iframe did not load ' +
            url +
            '. Layer points were not invented. The page was identified as Overwatch OSINT before embed.',
        }),
        jsx(Button, {
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            haptic('tap')
            remountFrame()
          },
          children: 'Retry',
        }),
      ],
    })
  }
  return jsx('iframe', {
    key: String(nonce) + ':' + url,
    id: 'smf-overwatch-pane-frame',
    src: url,
    title: 'Overwatch OSINT',
    className: 'min-h-0 w-full flex-1 border-0 bg-black',
    sandbox: IFRAME_SANDBOX,
    allow: 'clipboard-read; clipboard-write',
    referrerPolicy: 'no-referrer',
    onError: () => {
      $iframeFailedUrl.set(url)
    },
  })
}

function DistinctionNote() {
  return jsx('div', {
    className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
    children:
      'This pane iframes the Overwatch HUD only when :4173 or :5173 returns a page titled Overwatch OSINT. AIGC Studio (' +
      AIGC_STUDIO +
      ') and the AIGC pack builder are different apps. A pack builder or sparkDash on :5173 is not the HUD. /layers needs the plugin in that profile; the iframe does not.',
  })
}

function Actions({ hud, checking, onRetry }) {
  const github = (hud && hud.github_url) || GITHUB_URL
  const openUrl = verifiedHudUrl(hud)
  const knownDown = Boolean(hud && hud.identified === false)
  return jsxs('div', {
    className: 'flex flex-col gap-2 px-4',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2',
        children: [
          openUrl
            ? jsx(Button, {
                variant: 'ghost',
                size: 'sm',
                onClick: () => {
                  haptic('tap')
                  openExternal(openUrl)
                },
                children: jsxs('span', {
                  className: 'inline-flex items-center gap-1.5',
                  children: [jsx(Codicon, { name: 'link-external', size: 14 }), 'Open Overwatch HUD'],
                }),
              })
            : null,
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
          onRetry
            ? jsx(Button, {
                variant: 'ghost',
                size: 'sm',
                onClick: () => {
                  haptic('tap')
                  onRetry()
                },
                children: 'Retry',
              })
            : null,
        ],
      }),
      checking
        ? jsx('div', {
            className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
            children: 'Checking whether :4173 or :5173 is the Overwatch HUD…',
          })
        : null,
      openUrl
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
            children: `Overwatch HUD ${openUrl}`,
          })
        : knownDown
          ? jsx('div', {
              className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
              children:
                'Overwatch HUD was not identified on :4173 or :5173. Those ports are opened only when the page title is Overwatch OSINT.',
            })
          : null,
      jsx(DistinctionNote, {}),
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

function LayersStrip({ visible }) {
  return jsx(ScrollArea, {
    className: 'max-h-52 min-h-0 shrink-0 border-b border-(--ui-stroke-secondary)',
    children: jsx('div', {
      className: 'flex flex-col gap-2 px-3 py-2',
      children: visible.map((layer) => jsx(LayerRow, { layer }, layer.id)),
    }),
  })
}

function EmbedChrome({
  embedUrl,
  summary,
  layersProblem,
  unmounted,
  detail,
  isFetching,
  canToggleLayers,
  onRefresh,
  onRetry,
}) {
  const layersOpen = useValue($layersOpen)
  return jsxs('div', {
    className: 'flex shrink-0 flex-col gap-1.5 px-3 py-2',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Codicon, { name: 'globe', size: 16 }),
          jsx('div', {
            className: 'min-w-0 truncate text-sm font-medium tracking-[0.12em]',
            style: { color: HUD.cyan },
            children: 'OVERWATCH',
          }),
          jsx(Badge, { className: 'shrink-0 text-[0.625rem]', children: 'HUD' }),
          layersProblem
            ? jsx(BackendBadge, { unmounted, detail })
            : null,
          jsx('div', { className: 'min-w-0 flex-1' }),
          isFetching
            ? jsx('span', { className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: 'updating' })
            : null,
          canToggleLayers
            ? jsx(Button, {
                variant: 'ghost',
                size: 'sm',
                onClick: () => {
                  haptic('tap')
                  $layersOpen.set(!layersOpen)
                },
                children: layersOpen ? 'Hide layers' : 'Layers',
              })
            : null,
          jsx(Button, {
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              haptic('tap')
              openExternal(embedUrl)
            },
            children: jsxs('span', {
              className: 'inline-flex items-center gap-1.5',
              children: [jsx(Codicon, { name: 'link-external', size: 14 }), 'Open'],
            }),
          }),
          jsx(Button, {
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              haptic('tap')
              if (layersProblem) onRetry()
              else onRefresh()
            },
            children: layersProblem ? 'Retry' : 'Refresh',
          }),
        ],
      }),
      summary ? jsx(SummaryChips, { summary }) : null,
      layersProblem
        ? jsx('div', {
            className: 'truncate text-[0.625rem] text-(--ui-text-tertiary)',
            title: unmounted ? UNMOUNTED_COPY : detail,
            children: unmounted
              ? 'Quit and relaunch Hermes for /layers. The HUD embed does not need plugin_api.'
              : detail,
          })
        : null,
    ],
  })
}

function CheckingHud({ label }) {
  return jsxs('div', {
    className: 'flex flex-1 flex-col items-center justify-center gap-3',
    children: [
      jsx(GlyphSpinner, { size: 24 }),
      jsx('div', {
        className: 'px-6 text-center text-sm text-(--ui-text-secondary)',
        children: label,
      }),
    ],
  })
}

function OverwatchPane({ ctx }) {
  const queryClient = useQueryClient()
  const layersOpen = useValue($layersOpen)
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: [ID, 'layers'],
    queryFn: () => fetchLayers(ctx, false),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
    retry: 1,
  })
  const apiHud = data && data.hud
  const apiChecked = Boolean(apiHud && typeof apiHud.identified === 'boolean')
  // Identity probe is independent of plugin_api. Do not wait for /layers.
  const clientProbe = useQuery({
    queryKey: [ID, 'hud-identity'],
    queryFn: async () => ({ url: (await probeHudIdentity()) || '' }),
    enabled: !apiChecked,
    staleTime: 30 * 1000,
    retry: 0,
  })
  const clientPayload =
    clientProbe.data && typeof clientProbe.data.url === 'string' ? clientProbe.data : null
  const clientUrl = clientPayload && clientPayload.url ? clientPayload.url : null
  const clientFailed = Boolean(clientProbe.isError || clientProbe.error)
  const clientSettled = Boolean(!apiChecked && (clientPayload || clientFailed))
  const checkingHud = Boolean(!apiChecked && !clientSettled)
  const hud = resolveHud(apiHud, clientUrl, clientSettled)
  const embedUrl = verifiedHudUrl(hud)
  const retryAll = () => {
    void refetch()
    if (typeof clientProbe.refetch === 'function') void clientProbe.refetch()
    remountFrame()
  }
  const refreshLayers = () => {
    fetchLayers(ctx, true)
      .then((fresh) => {
        queryClient.setQueryData([ID, 'layers'], fresh)
      })
      .catch(() => {
        void refetch()
      })
  }
  const layers = asLayers(data)
  const unread = Boolean(error && !data) || isUnread(data)
  const ordered = LAYER_ORDER.map((id) => layers.find((row) => row.id === id)).filter(Boolean)
  const extras = layers.filter((row) => !LAYER_ORDER.includes(row.id))
  const visible = ordered.concat(extras)
  const layersProblem = unread || (hasReadProblems(data) && visible.length === 0)
  const unmounted = Boolean((error && !data) || (data && data.read_status === 'unread'))
  const detail = layersProblem && !unmounted ? formatErrors(data) : ''
  const summary = data && !layersProblem ? data.summary : null

  if (embedUrl) {
    return jsxs('div', {
      className: 'flex h-full min-h-0 flex-col bg-(--ui-bg)',
      children: [
        jsx(EmbedChrome, {
          embedUrl,
          summary,
          layersProblem,
          unmounted,
          detail,
          isFetching,
          canToggleLayers: visible.length > 0,
          onRefresh: refreshLayers,
          onRetry: retryAll,
        }),
        layersOpen && visible.length
          ? jsx(LayersStrip, { visible })
          : null,
        jsx(HudFrame, { url: embedUrl }),
      ],
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col gap-3',
    children: [
      layersProblem
        ? jsxs('div', {
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
                  jsx(BackendBadge, { unmounted, detail }),
                ],
              }),
              checkingHud
                ? null
                : jsx('div', {
                    className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
                    children: unmounted ? UNMOUNTED_COPY : detail,
                  }),
            ],
          })
        : data
          ? jsx(HudHeader, { data, hud, isFetching, onRefresh: refreshLayers })
          : jsxs('div', {
              className: 'flex items-center gap-2 px-4 pt-4',
              children: [
                jsx(Codicon, { name: 'globe', size: 16 }),
                jsx('div', {
                  className: 'text-sm font-medium tracking-[0.12em]',
                  style: { color: HUD.cyan },
                  children: 'OVERWATCH',
                }),
              ],
            }),
      jsx(Actions, { hud, checking: checkingHud, onRetry: retryAll }),
      !layersProblem && data ? jsx(CasesTip, { hud }) : null,
      !layersProblem && data ? jsx(Separator, {}) : null,
      checkingHud
        ? jsx(CheckingHud, {
            label: 'Checking whether :4173 or :5173 is the Overwatch HUD…',
          })
        : isLoading && !data
          ? jsx(CheckingHud, { label: 'Layer status is still loading. The HUD was not identified.' })
          : layersProblem
          ? null
          : visible.length === 0
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
        // Sole layout pane. Do not add a second PANES_AREA entry — an empty
        // placement:'floating' keep still draws a titled card over the HUD.
        // Close on this pane disables the plugin (Hermes sole-pane rule).
        id: 'pane',
        area: PANES_AREA,
        title: 'Overwatch',
        data: {
          placement: 'right',
          width: '760px',
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
          label: 'Open Overwatch HUD',
          keywords: ['overwatch', 'hud', 'omarchy', 'osint', 'globe'],
          run: () => {
            probeHudIdentity().then((url) => {
              if (url) openExternal(url)
              else host.navigate(ROUTE)
            })
          },
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
