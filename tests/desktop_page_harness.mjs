// The WPBrücke page off-device: a small stand-in for react, react/jsx-runtime and @hermes/plugin-sdk
// (adapted from Coolifybrücke's tests/desktop_page_harness.mjs), enough to mount the real
// desktop/plugin.js and drive it like a user, in plain node and in Chromium.
//
//   node desktop_page_harness.mjs probe                      -> {"playwright": path or null}
//   node desktop_page_harness.mjs node <plugin.js> [case..]  -> one line RESULT {...} per case
//   node desktop_page_harness.mjs chromium <plugin.js> <out> -> RESULT lines, screenshots in <out>
//
// A case is <scenario>/<shim>. Two SDK shims: `with` exports captureGatewayFileDownload, `without`
// does not (an older Desktop). The page reads optional SDK names through a namespace import, so a
// named import of a missing export would fail the module here exactly as in Desktop: node's and
// Chromium's ESM linkers both refuse it. In node every case runs in its own process (the page keeps
// module state); in Chromium each case is a fresh page whose import map points `react` and
// `react/jsx-runtime` at this file and `@hermes/plugin-sdk` at the chosen shim, and plugin.js is
// served unchanged.
//
// What the stand-in keeps from the real libraries: hooks keep their state per component instance,
// effects run after a render and clean up; React Query's per-key cache with `enabled`, `retry` and a
// shared fetch in flight. In Chromium each render is drawn into real DOM (rebuilt whole, so focus is
// not kept) with the page's own stylesheet and, when WPB_DESKTOP_CSS names Desktop's compiled
// main-*.css, the app's theme variables and codicons. Layout, Radix behaviour and the real
// components are not reproduced; the Mac check proves those.
//
// Environment: PLAYWRIGHT_FROM (a folder whose node_modules holds playwright; else node's own
// lookup), WPB_CHROMIUM (the Chromium binary; else Playwright's own), WPB_DESKTOP_CSS (optional).

const IN_BROWSER = typeof document !== 'undefined' && typeof document.createElement === 'function'

// ── node: a window for the page's hash route; window.open is recorded ────────────────────────────
export const windowOpens = []
const windowListeners = new Map()
if (!IN_BROWSER) {
  globalThis.window = {
    setTimeout, clearTimeout, setInterval, clearInterval,
    open: (...args) => (windowOpens.push(args), null),
    location: { hash: '' },
    addEventListener: (type, fn) => windowListeners.set(type, (windowListeners.get(type) || new Set()).add(fn)),
    removeEventListener: (type, fn) => windowListeners.has(type) && windowListeners.get(type).delete(fn)
  }
}

export function setHash(hash) {
  const next = hash.startsWith('#') ? hash : '#' + hash
  if (IN_BROWSER) {
    window.location.hash = next
    return
  }
  if (next === window.location.hash) return
  window.location.hash = next
  for (const fn of windowListeners.get('hashchange') || []) fn({ type: 'hashchange' })
}

// ── react ────────────────────────────────────────────────────────────────────────────────────
const instances = new Map()
let visited = new Set()
let current = null
let hookIndex = 0
let effects = []
let rootElement = null
let renderQueued = false
let renders = 0

export function jsx(type, props, key) {
  return { type, props: props || {}, key: key === undefined ? null : key }
}
export const jsxs = jsx

export class Component {
  constructor(props) {
    this.props = props
    this.state = {}
  }

  setState(next) {
    this.state = { ...this.state, ...(typeof next === 'function' ? next(this.state, this.props) : next) }
    schedule()
  }
}
Component.prototype.isReactComponent = {}

function slot(init) {
  const inst = current
  const i = hookIndex++
  if (!(i in inst.hooks)) inst.hooks[i] = init()
  return inst.hooks[i]
}

const changed = (a, b) => !a || !b || a.length !== b.length || a.some((v, i) => !Object.is(v, b[i]))

export function useState(init) {
  const s = slot(() => ({ value: typeof init === 'function' ? init() : init }))
  if (!s.set) {
    s.set = next => {
      const value = typeof next === 'function' ? next(s.value) : next
      if (!Object.is(value, s.value)) {
        s.value = value
        schedule()
      }
    }
  }
  return [s.value, s.set]
}

export function useRef(value) {
  return slot(() => ({ current: value }))
}

export function useMemo(fn, deps) {
  const s = slot(() => ({ deps: null }))
  if (s.deps === null || changed(s.deps, deps)) {
    s.value = fn()
    s.deps = deps
  }
  return s.value
}

export function useEffect(fn, deps) {
  const s = slot(() => ({ deps: null, effect: true }))
  if (s.deps === null || deps === undefined || changed(s.deps, deps)) {
    s.deps = deps || []
    effects.push(() => {
      if (s.cleanup) s.cleanup()
      const out = fn()
      s.cleanup = typeof out === 'function' ? out : null
    })
  }
}

const hosts = new Map()
let seenHosts = new Set()

function expand(node, path, parent) {
  if (node == null || typeof node === 'boolean') return []
  if (typeof node === 'string' || typeof node === 'number') return [String(node)]
  if (Array.isArray(node)) return node.flatMap((child, i) => expand(child, path + '/' + (child && child.key != null ? 'k:' + child.key : i), parent))
  if (typeof node.type === 'function' && node.type.prototype && node.type.prototype.isReactComponent) {
    const id = path + ':' + (node.type.name || 'anonymous') + (node.key != null ? '#' + node.key : '')
    let inst = instances.get(id)
    if (!inst) instances.set(id, (inst = { hooks: [], obj: new node.type(node.props) }))
    visited.add(id)
    inst.obj.props = node.props
    try {
      return expand(inst.obj.render(), id, parent)
    } catch (error) {
      if (typeof node.type.getDerivedStateFromError !== 'function') throw error
      inst.obj.state = { ...inst.obj.state, ...node.type.getDerivedStateFromError(error) }
      if (typeof inst.obj.componentDidCatch === 'function') inst.obj.componentDidCatch(error)
      return expand(inst.obj.render(), id, parent)
    }
  }
  if (typeof node.type === 'function') {
    const id = path + ':' + (node.type.name || 'anonymous') + (node.key != null ? '#' + node.key : '')
    let inst = instances.get(id)
    if (!inst) instances.set(id, (inst = { hooks: [] }))
    visited.add(id)
    const saved = [current, hookIndex]
    current = inst
    hookIndex = 0
    let out
    try {
      out = node.type(node.props)
    } finally {
      ;[current, hookIndex] = saved
    }
    return expand(out, id, parent)
  }
  const hid = path + '>' + node.type
  let host = hosts.get(hid)
  if (!host || host.type !== node.type) {
    host = { type: node.type, hid }
    hosts.set(hid, host)
  }
  seenHosts.add(hid)
  Object.assign(host, { props: node.props, parent })
  if (node.props.ref && typeof node.props.ref === 'object') node.props.ref.current = host
  host.children = expand(node.props.children, hid, host)
  return [host]
}

let tree = []
function render() {
  renders += 1
  visited = new Set()
  effects = []
  seenHosts = new Set()
  tree = expand(rootElement, 'root', null)
  for (const hid of [...hosts.keys()]) if (!seenHosts.has(hid)) hosts.delete(hid)
  for (const [id, inst] of instances) {
    if (visited.has(id)) continue
    for (const h of inst.hooks) if (h && h.cleanup) h.cleanup()
    instances.delete(id)
  }
  if (domRoot) commitDom()
  const run = effects
  effects = []
  for (const fn of run) fn()
}

function schedule() {
  if (renderQueued || !rootElement) return
  renderQueued = true
  queueMicrotask(() => {
    renderQueued = false
    render()
  })
}

export function mount(element) {
  rootElement = element
  render()
}

// ── the DOM, Chromium only: every render rebuilds the container ──────────────────────────────
let domRoot = null
const UNITLESS = new Set(['flex', 'flexGrow', 'flexShrink', 'fontWeight', 'lineHeight', 'opacity', 'order', 'zIndex', 'zoom'])
const EVENTS = { onClick: 'click', onChange: 'input', onInput: 'input', onKeyDown: 'keydown', onBlur: 'blur', onFocus: 'focus', onSubmit: 'submit' }

function toDom(node) {
  if (typeof node === 'string') return document.createTextNode(node)
  const out = document.createElement(node.type)
  for (const [k, v] of Object.entries(node.props)) {
    if (k === 'children' || k === 'key' || k === 'ref' || v === undefined || v === null) continue
    if (typeof v === 'function') {
      if (EVENTS[k]) out.addEventListener(EVENTS[k], e => v(e))
    } else if (k === 'className') out.setAttribute('class', String(v))
    else if (k === 'style' && typeof v === 'object') {
      for (const [sk, sv] of Object.entries(v)) out.style[sk] = typeof sv === 'number' && !UNITLESS.has(sk) ? sv + 'px' : sv
    } else if (k === 'value' || k === 'defaultValue') out.value = String(v)
    else if (k === 'htmlFor') out.setAttribute('for', String(v))
    else if (/^(aria|data)-/.test(k)) out.setAttribute(k, String(v))
    else if (v === true) out.setAttribute(k, '')
    else if (v !== false) out.setAttribute(k, String(v))
  }
  for (const child of node.children) out.appendChild(toDom(child))
  return out
}

function commitDom() {
  domRoot.replaceChildren(...tree.map(toDom))
}

export function mountDom(element, container) {
  domRoot = container
  mount(element)
}

// ── nanostores ───────────────────────────────────────────────────────────────────────────────
export function atom(value) {
  const listeners = new Set()
  return {
    get: () => value,
    set: next => {
      if (Object.is(next, value)) return
      value = next
      listeners.forEach(l => l(value))
      schedule()
    },
    listen: l => (listeners.add(l), () => listeners.delete(l)),
    subscribe: l => (listeners.add(l), l(value), () => listeners.delete(l))
  }
}
export const useValue = a => (a && typeof a.get === 'function' ? a.get() : a)

// ── react query ──────────────────────────────────────────────────────────────────────────────
const entries = new Map()
let inflight = 0

function entryFor(queryKey) {
  const k = JSON.stringify(queryKey)
  let e = entries.get(k)
  if (!e) {
    e = { k, queryKey, state: { data: undefined, error: null }, fetching: null, fetched: false, opts: null }
    entries.set(k, e)
  }
  return e
}

function fetchEntry(e, opts) {
  if (e.fetching) return e.fetching
  const o = opts || e.opts
  inflight += 1
  schedule()
  e.fetching = (async () => {
    let failures = 0
    for (;;) {
      try {
        const data = await o.queryFn()
        e.state = { data, error: null }
        break
      } catch (error) {
        const r = o.retry
        const again = typeof r === 'function' ? r(failures, error) : typeof r === 'number' ? failures < r : r !== false
        failures += 1
        if (!again) {
          e.state = { ...e.state, error }
          break
        }
      }
    }
  })().finally(() => {
    e.fetching = null
    e.fetched = true
    inflight -= 1
    schedule()
  })
  return e.fetching
}

export function useQuery(opts) {
  const e = entryFor(opts.queryKey)
  e.opts = opts
  const enabled = opts.enabled !== false
  if (enabled && !e.fetched && !e.fetching) void fetchEntry(e, opts)
  const { data, error } = e.state
  const fetching = Boolean(e.fetching)
  return {
    data,
    error,
    isError: Boolean(error),
    isFetching: fetching,
    isLoading: enabled && data === undefined && !error && (fetching || !e.fetched),
    refetch: () => fetchEntry(e, opts)
  }
}

const startsWith = (key, prefix) => prefix.every((v, i) => JSON.stringify(v) === JSON.stringify(key[i]))

const client = {
  invalidateQueries: async ({ queryKey } = {}) => {
    const hits = [...entries.values()].filter(e => !queryKey || startsWith(e.queryKey, queryKey))
    for (const e of hits) e.fetched = false
    schedule()
  },
  cancelQueries: async () => {},
  setQueryData: (queryKey, updater) => {
    const e = entryFor(queryKey)
    e.state = { data: typeof updater === 'function' ? updater(e.state.data) : updater, error: null }
    e.fetched = true
    schedule()
  },
  getQueryData: queryKey => entryFor(queryKey).state.data,
  fetchQuery: ({ queryKey, queryFn }) => {
    const e = entryFor(queryKey)
    return fetchEntry(e, { ...(e.opts || {}), queryFn, retry: false }).then(() => {
      if (e.state.error) throw e.state.error
      return e.state.data
    })
  }
}
export const useQueryClient = () => client

/** Wait until no render is queued and no fetch is in flight. */
export async function settle() {
  for (let quiet = 0, n = 0; quiet < 3 && n < 500; n += 1) {
    await new Promise(r => setTimeout(r, 0))
    quiet = renderQueued || inflight ? 0 : quiet + 1
  }
}

// ── @hermes/plugin-sdk (the names plugin.js imports) ─────────────────────────────────────────
const UI_ONLY = ['variant', 'size', 'spinning', 'tone', 'sizing', 'onCheckedChange', 'checked']
const strip = props => Object.fromEntries(Object.entries(props).filter(([k]) => !UI_ONLY.includes(k)))

export const Codicon = props => jsx('i', { className: 'codicon codicon-' + props.name, 'data-icon': props.name, style: props.style })
export const Input = props => jsx('input', strip(props))
export const Switch = props => jsx('button', { type: 'button', role: 'switch', 'aria-checked': Boolean(props.checked), 'aria-label': props['aria-label'], onClick: () => props.onCheckedChange && props.onCheckedChange(!props.checked) })

export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const TRANSCRIPT_DIRECTIVE_AREA = 'transcript.directive'

export const hostCalls = []
const record = name => (...args) => {
  hostCalls.push({ name, args })
  return Promise.resolve(true)
}
export const host = {
  state: { profile: atom('default'), connectionId: atom('local') },
  navigate: record('navigate'),
  request: record('request'),
  openSession: record('openSession'),
  newChat: record('newChat'),
  notify: record('notify'),
  composer: { setDraft: record('setDraft') }
}

// Desktop's captureGatewayFileDownload (lib/media.ts): a capture returns the save function. The `with`
// shim exports this under the real name; every capture and every save is recorded.
export const downloads = { captures: 0, saves: [] }
export function fakeCaptureGatewayFileDownload() {
  downloads.captures += 1
  return async (path, suggestedName) => {
    downloads.saves.push([path, suggestedName])
  }
}

// ── reading the tree ─────────────────────────────────────────────────────────────────────────
export const text = n => (typeof n === 'string' ? n : n ? n.children.map(text).join('') : '')
export const pageText = () => tree.map(text).join('')

export function all(test, nodes = tree) {
  return nodes.flatMap(n => (typeof n === 'string' ? [] : [...(test(n) ? [n] : []), ...all(test, n.children)]))
}

const byTest = (name, site) => all(n => n.props['data-test'] === name && (site === undefined || n.props['data-site'] === site))
const buttonsIn = (nodes, label) => nodes.flatMap(root => all(n => n.type === 'button' && text(n).replace(' ↗', '').trim() === label, [root]))

export function click(node) {
  if (!node || node.props.disabled || typeof node.props.onClick !== 'function') return false
  node.props.onClick({ type: 'click', target: node, currentTarget: node, preventDefault() {}, stopPropagation() {} })
  return true
}

// ── fixtures (synthetic hosts under example.test) ────────────────────────────────────────────
const NOW_S = Date.now() / 1000

function siteRow(id, extra) {
  const url = 'https://' + id + '.example.test'
  return {
    id, url, label: id.charAt(0).toUpperCase() + id.slice(1), label_group: '', reachable: true, rest_ms: 320,
    wp_version: '7.1.4', theme: 'Twenty Twenty-Five', access: 'Read', drafts: 2, pending: 0, comments_hold: 0,
    published: 14, last_post: null, updates: 0, connector: null, group: 1, pairing: 'paired', effective: 'confirmed',
    level: 'read', level_words: 'read drafts and pages', noindex: false, fetched_at: new Date().toISOString(),
    desk_waiting: 0, mark: id.slice(0, 2).toUpperCase(), wp_admin_url: url + '/wp-admin/', cockpit_url: null,
    ...extra
  }
}

const FLEET = [
  siteRow('shop', { group: 1, health: { verdict: 'broken', since: NOW_S - 5400 }, builders: ['elementor', 'blocks'],
    packs: { generatepress: { label: 'GeneratePress', detected: false, on: false, needs: null } } }),
  siteRow('news', { group: 2, connector: '0.5.0', health: { verdict: 'updating', since: NOW_S - 120 }, builders: ['blocks'], packs: {} }),
  siteRow('blog', { group: 3, connector: '0.1.0' })
]

function overview(rows) {
  return {
    ok: true, mode: rows.length > 1 ? 'many' : 'one', desk_waiting: 0, sites: rows, needs_you: [],
    strip: { sites: rows.length, keyed: rows.length, waiting: 0, updates: 0, slow: 0 }, generated_at: new Date().toISOString()
  }
}

function groups(rows) {
  return {
    ok: true, free: rows.length + 1, used: rows.length, settings_href: '/settings?tab=plugins&agent=wpbruecke', profile: 'default',
    groups: rows.map((r, i) => ({ n: i + 1, url: r.url, user: 'hermes', set: true, fp: 'ab12cd34', state: 'paired', site_id: r.id,
      host: r.url.replace('https://', ''), error: null, check: null, migrate: null }))
  }
}

function siteDetail(row, connector) {
  return {
    ok: true,
    site: { id: row.id, url: row.url, label: row.label, key_slot: 1, username: 'hermes', automations: [], packs: { generatepress: false },
      access: { level: 'read', role: 'wpbruecke_reader', caps_fingerprint: 'ab12', confirmed_at: new Date().toISOString() } },
    facts: { reachable: true, name: row.label, connector_ns: Boolean(connector), connector: connector ? { version: connector } : undefined },
    rights: ['read'], available: [], not_available: [], versions: [], yaml: 'id: ' + row.id + '\nurl: ' + row.url + '\n', settings_url: '/settings'
  }
}

const PACKAGE = { ok: true, path: '/hermes/plugin-data/agent-plugin-wpbruecke-0000/connector/wpbruecke-connector.zip',
  filename: 'wpbruecke-connector-0.5.0.zip', version: '0.5.0', sha256: '0'.repeat(64), bytes: 21000, workspace: 'host' }

// The site's write log as GET /sites/{id}/activity answers it: newest first.
const ACTIVITY = {
  ok: true, site: 'shop', limit: 50,
  lines: [
    { ts: new Date(Date.now() - 120000).toISOString().slice(0, 19) + 'Z', site: 'shop', tool: 'wp_draft', action: 'stage', wp_type: 'page', wp_id: 12,
      label: 'About us', desk_item: 'item-20261125-1', lane: 'chat', who: 'agent:sess-1', result: 'ok', code: null,
      summary: 'staged a change of page 12 as draft copy 57 (title); title "About us" → "About the studio"; the live page is unchanged', read_back: null },
    { ts: new Date(Date.now() - 3600000).toISOString().slice(0, 19) + 'Z', site: 'shop', tool: 'wp_draft', action: 'update', wp_type: 'post', wp_id: 40,
      label: 'Five things', desk_item: null, lane: 'chat', who: 'agent', result: 'refused', code: 'live_post',
      summary: 'update post 40 refused: post 40 is publish; use action=stage for a draft copy', read_back: null },
    { ts: new Date(Date.now() - 90000000).toISOString().slice(0, 19) + 'Z', site: 'shop', tool: 'page', action: 'publish', wp_type: 'post', wp_id: 41,
      label: 'Hello world', desk_item: 'item-20261124-2', lane: 'desk', who: 'person', result: 'ok', code: null,
      summary: 'published post 41; WordPress reads it back as publish', read_back: { status: 'publish' } }
  ]
}

// scenario -> { hash, rows, connector (the settings site's), pkg (POST answer or an Error) }
const SCENARIOS = {
  'settings-missing': { hash: '/wpbruecke?view=settings&site=shop', rows: [siteRow('shop')], connector: null },
  'settings-older': { hash: '/wpbruecke?view=settings&site=shop', rows: [siteRow('shop', { connector: '0.1.0' })], connector: '0.1.0' },
  'settings-current': { hash: '/wpbruecke?view=settings&site=shop', rows: [siteRow('shop', { connector: '0.5.0' })], connector: '0.5.0' },
  'settings-ssh': { hash: '/wpbruecke?view=settings&site=shop', rows: [siteRow('shop')], connector: null, pkg: { ...PACKAGE, workspace: 'ssh' } },
  'settings-fail': { hash: '/wpbruecke?view=settings&site=shop', rows: [siteRow('shop')], connector: null,
    pkg: new Error('500: {"ok": false, "error": "zip_failed", "message": "The connector zip could not be written: disk full"}') },
  fleet: { hash: '/wpbruecke', rows: FLEET, connector: null },
  'one-site': { hash: '/wpbruecke', rows: [FLEET[0]], connector: null },
  'activity-closed': { hash: '/wpbruecke', rows: [siteRow('shop')], connector: null },
  'activity-open': { hash: '/wpbruecke', rows: [siteRow('shop')], connector: null }
}

export const CASES = [
  'settings-missing/with', 'settings-missing/without', 'settings-older/with', 'settings-older/without',
  'settings-current/with', 'settings-ssh/with', 'settings-fail/with', 'fleet/with', 'fleet/without', 'one-site/without',
  'activity-closed/with', 'activity-open/with'
]

// ── one case: mount the page, check what a person sees ───────────────────────────────────────
export async function runCase(plugin, scenarioName, shim, { container } = {}) {
  const sc = SCENARIOS[scenarioName]
  if (!sc) throw new Error('unknown scenario ' + scenarioName)
  const restCalls = []
  const opened = []
  const contributions = []
  const answer = path => {
    const p = path.split('?')[0]
    if (p === '/overview') return overview(sc.rows)
    if (/^\/sites\/[^/]+\/activity$/.test(p)) return ACTIVITY
    if (p === '/groups') return groups(sc.rows)
    if (p.startsWith('/sites/')) {
      const row = sc.rows.find(r => r.id === decodeURIComponent(p.slice(7))) || sc.rows[0]
      return siteDetail(row, sc.connector)
    }
    if (p === '/connector-package') {
      if (sc.pkg instanceof Error) throw sc.pkg
      return sc.pkg || PACKAGE
    }
    if (p === '/desk') return { ok: true, items: [], counts: { waiting: 0 } }
    if (p === '/seo') return { ok: true, reports: [], site_report: null }
    if (p === '/build') return { ok: true, runs: [] }
    return { ok: true }
  }
  const ctx = {
    rest: async (path, opts) => {
      restCalls.push(((opts && opts.method) || 'GET') + ' ' + path)
      await new Promise(r => setTimeout(r, 0))
      return JSON.parse(JSON.stringify(answer(path)))
    },
    register: c => (contributions.push(c), () => {}),
    registerSettingsPage: () => () => {},
    onDispose: () => {},
    setInterval: () => () => {},
    storage: { get: (key, fallback) => fallback, set: () => {} },
    os: { openExternal: async href => (opened.push(href), true) }
  }
  setHash(sc.hash)
  plugin.register(ctx)
  const page = contributions.find(c => c.area === ROUTES_AREA)
  if (container) mountDom(page.render(), container)
  else mount(page.render())
  await settle()

  const got = { scenario: scenarioName, shim, checks: {}, rest: restCalls }
  const check = (name, ok, detail) => {
    got.checks[name] = ok ? true : detail === undefined ? false : detail
  }
  const body = pageText()
  check('no error view', !body.includes('This view hit an error'), body.slice(0, 300))
  const postCount = () => restCalls.filter(c => c === 'POST /connector-package').length

  if (scenarioName.startsWith('settings-')) {
    const line = byTest('connector-line').map(text).join(' ')
    const getBlock = byTest('connector-get')
    const cli = byTest('connector-cli').map(text).join(' ')
    const download = buttonsIn(getBlock, 'Download connector')
    const install = buttonsIn(getBlock, 'Install in WordPress')
    got.connector = { line, cli, download: download.length, install: install.length }
    const wantsGet = scenarioName === 'settings-missing' || scenarioName === 'settings-older' || scenarioName === 'settings-ssh' || scenarioName === 'settings-fail'
    if (scenarioName === 'settings-older') check('badge older', line.includes('v0.1.0 · older than needed'), line)
    else if (scenarioName === 'settings-current') check('badge current', line.includes('v0.5.0') && !line.includes('older'), line)
    else check('badge not installed', line.includes('not installed'), line)
    if (!wantsGet) {
      check('nothing to get', getBlock.length === 0 && postCount() === 0, { blocks: getBlock.length, posts: postCount() })
    } else {
      check('install link', install.length === 1)
      const buttonExpected = shim === 'with' && (scenarioName === 'settings-missing' || scenarioName === 'settings-older')
      if (buttonExpected) {
        check('download button', download.length === 1 && !download[0].props.disabled && !cli, got.connector)
        check('one POST at render', postCount() === 1, restCalls)
        check('captured with the read', downloads.captures === 1 && downloads.saves.length === 0, { ...downloads })
        click(download[0])
        await settle()
        check('click saves the zip', downloads.saves.length === 1 && downloads.saves[0][0] === PACKAGE.path && downloads.saves[0][1] === PACKAGE.filename, downloads.saves)
        check('no capture at click', downloads.captures === 1 && postCount() === 1, { ...downloads, posts: postCount() })
      } else {
        check('no download button', download.length === 0, got.connector)
        check('CLI sentence', cli.includes('hermes wpbruecke connector-zip --out PATH') && cli.includes('upload the zip in WordPress'), cli)
        if (shim === 'without') check('no POST without the export', postCount() === 0 && downloads.captures === 0, restCalls)
        if (scenarioName === 'settings-ssh') check('says SSH', cli.includes('SSH machine'), cli)
        if (scenarioName === 'settings-fail') check('says why', cli.includes('disk full'), cli)
      }
      click(install[0])
      await settle()
      check('install opens upload page', opened.includes('https://shop.example.test/wp-admin/plugin-install.php?tab=upload'), opened)
    }
  }

  if (scenarioName === 'fleet' || scenarioName === 'one-site') {
    const scope = id => (scenarioName === 'fleet' ? byTest('site-card', id) : byTest('site-home', id))
    const badges = id => scope(id).flatMap(c => [...all(n => n.props['data-test'] === 'health-badge', [c]), ...all(n => n.props['data-test'] === 'builder-badge', [c])]).map(text)
    const installs = id => buttonsIn(scope(id), 'Install in WordPress')
    got.cards = Object.fromEntries(sc.rows.map(r => [r.id, { badges: badges(r.id), install: installs(r.id).length }]))
    check('shop: broken + Elementor no pack', JSON.stringify(badges('shop')) === JSON.stringify(['Broken', 'Elementor · no pack']), badges('shop'))
    check('shop: install link', installs('shop').length === 1)
    if (scenarioName === 'fleet') {
      check('news: updating only', JSON.stringify(badges('news')) === JSON.stringify(['Updating']), badges('news'))
      check('news: current connector, no install', installs('news').length === 0)
      check('blog: no keys, no badges', badges('blog').length === 0, badges('blog'))
      check('blog: older connector, install link', installs('blog').length === 1)
    }
    click(installs('shop')[0])
    await settle()
    check('install opens upload page', JSON.stringify(opened) === JSON.stringify(['https://shop.example.test/wp-admin/plugin-install.php?tab=upload']), opened)
    check('no connector POST on the fleet', postCount() === 0, restCalls)
  }

  const activityCalls = () => restCalls.filter(c => /\/activity$/.test(c))
  if (scenarioName === 'fleet') {
    check('no activity on the fleet', byTest('activity').length === 0 && activityCalls().length === 0, { calls: activityCalls() })
  }
  if (scenarioName.startsWith('activity-')) {
    const section = byTest('activity')
    const toggle = byTest('activity-toggle')
    const home = byTest('site-home', 'shop')
    const last = home.length ? home[0].children[home[0].children.length - 1] : null
    check('disclosure at the foot of the site page', section.length === 1 && last === section[0], { sections: section.length })
    check('closed by default', toggle.length === 1 && toggle[0].props['aria-expanded'] === false && section[0].props['data-open'] === 'false')
    check('no read of the log while closed', activityCalls().length === 0, restCalls)
    check('no badge, no count', text(toggle[0]).trim() === 'Activity', text(toggle[0]))
    if (scenarioName === 'activity-open') {
      click(toggle[0])
      await settle()
      const rows = byTest('activity-line').map(text)
      got.activity = rows
      check('opened', byTest('activity-toggle')[0].props['aria-expanded'] === true)
      check('one read when opened', JSON.stringify(activityCalls()) === JSON.stringify(['GET /sites/shop/activity']), activityCalls())
      check('lines newest first in plain words', rows.length === 3 && rows[0].startsWith('2 min ago') &&
        rows[0].includes('Change staged as a draft copy “About us” · page 12, by Hermes') &&
        rows[1].includes('Refused: draft changed (live_post) “Five things” · post 40') &&
        rows[2].includes('Published “Hello world” · post 41, by you'), rows)
      check('still no count', text(byTest('activity-toggle')[0]).trim() === 'Activity')
    }
  }
  check('no window.open', windowOpens.length === 0, windowOpens)
  got.ok = Object.values(got.checks).every(v => v === true)
  return got
}

// In Chromium: the entry module calls this; the runner reads window.__WPB_RESULT.
export async function browserCase(plugin, scenarioName, shim) {
  try {
    window.__WPB_RESULT = await runCase(plugin, scenarioName, shim, { container: document.getElementById('root') })
  } catch (error) {
    window.__WPB_RESULT = { ok: false, scenario: scenarioName, shim, error: String((error && error.stack) || error) }
  }
}

// ── the runner (node) ────────────────────────────────────────────────────────────────────────
const SPECIFIERS = ['@hermes/plugin-sdk', 'react/jsx-runtime', 'react']
const SHIM_SOURCE = {
  with: "export * from './harness.mjs'\nexport { fakeCaptureGatewayFileDownload as captureGatewayFileDownload } from './harness.mjs'\n",
  without: "export * from './harness.mjs'\n"
}

async function prepare(pluginPath) {
  const fs = await import('node:fs')
  const os = await import('node:os')
  const path = await import('node:path')
  const work = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), 'wpb-page-'))
  fs.copyFileSync(import.meta.filename, path.join(work, 'harness.mjs'))
  const js = fs.readFileSync(pluginPath, 'utf8')
  for (const s of SPECIFIERS) if (!js.includes(`from '${s}'`)) throw new Error('plugin.js no longer imports ' + s)
  fs.writeFileSync(path.join(work, 'plugin.js'), js)
  for (const shim of ['with', 'without']) {
    fs.writeFileSync(path.join(work, `sdk-${shim}.mjs`), SHIM_SOURCE[shim])
    let local = js
    for (const s of SPECIFIERS) local = local.split(`from '${s}'`).join(`from '${s === '@hermes/plugin-sdk' ? `./sdk-${shim}.mjs` : './harness.mjs'}'`)
    fs.writeFileSync(path.join(work, `plugin-${shim}.mjs`), local)
  }
  return work
}

// One case in this process: `node <work>/harness.mjs case <scenario> <shim>`.
async function caseMain(scenarioName, shim) {
  const errors = []
  const origError = console.error
  console.error = (...args) => {
    errors.push(args.map(String).join(' '))
    origError(...args)
  }
  process.on('unhandledRejection', e => errors.push('unhandled rejection: ' + String(e && e.stack ? e.stack : e)))
  let result
  try {
    const plugin = (await import(`./plugin-${shim}.mjs`)).default
    result = await runCase(plugin, scenarioName, shim)
  } catch (error) {
    result = { ok: false, scenario: scenarioName, shim, error: String((error && error.stack) || error) }
  }
  await new Promise(r => setTimeout(r, 20))
  if (errors.length) {
    result.ok = false
    result.consoleErrors = errors
  }
  process.stdout.write('RESULT ' + JSON.stringify(result) + '\n')
}

async function nodeMain(pluginPath, cases) {
  const { spawnSync } = await import('node:child_process')
  const path = await import('node:path')
  const work = await prepare(pluginPath)
  for (const c of cases.length ? cases : CASES) {
    const [scenario, shim] = c.split('/')
    const done = spawnSync(process.execPath, [path.join(work, 'harness.mjs'), 'case', scenario, shim], { cwd: work, encoding: 'utf8', timeout: 60000 })
    const line = (done.stdout || '').split('\n').find(l => l.startsWith('RESULT '))
    process.stdout.write(line ? line + '\n' : 'RESULT ' + JSON.stringify({ ok: false, scenario, shim, error: 'no result', stdout: done.stdout, stderr: done.stderr, status: done.status }) + '\n')
  }
  ;(await import('node:fs')).rmSync(work, { recursive: true, force: true })
}

async function loadPlaywright() {
  const { createRequire } = await import('node:module')
  const path = await import('node:path')
  const from = process.env.PLAYWRIGHT_FROM
  const req = createRequire(from ? path.join(path.resolve(from), 'noop.js') : import.meta.filename)
  try {
    const resolved = req.resolve('playwright')
    return { path: resolved, module: req('playwright') }
  } catch {
    return null
  }
}

// Dark theme seeds in the range Desktop's theme context sets (as Coolifybrücke's render harness does).
const DARK = '--theme-foreground:#e6e8eb;--theme-primary:#4d84ff;--theme-midground:#4d84ff;--theme-secondary:#1c2433;--theme-accent-soft:#1a2232;--theme-background-seed:#111317;--theme-sidebar-seed:#0c0e11;--theme-card-seed:#14161a;--theme-elevated-seed:#1b1e23;--theme-bubble-seed:#1b1e23;--dt-foreground:#e6e8eb;--dt-background:#111317;--dt-card:#16181c;--dt-primary:#4d84ff;--dt-primary-foreground:#ffffff;--dt-muted:#22252b;--dt-muted-foreground:#9aa0a8;--dt-destructive:#e5484d;--dt-ring:#4d84ff;--dt-border:#2a2d33;--dt-input:#2a2d33;--dt-accent:#22252b;color-scheme:dark'

// The evidence: one downscaled WebP per state, the element named by its selector.
const SHOTS = {
  'settings-missing/with': ['connector-button', '[data-test=settings] .wpb-panel'],
  'settings-missing/without': ['connector-cli', '[data-test=settings] .wpb-panel'],
  'fleet/with': ['card-broken-elementor', '[data-test=fleet]'],
  'activity-closed/with': ['activity-closed', '[data-test=site-home]'],
  'activity-open/with': ['activity-open', '[data-test=activity]']
}

async function chromiumMain(pluginPath, outDir) {
  const fs = await import('node:fs')
  const path = await import('node:path')
  const pw = await loadPlaywright()
  if (!pw) throw new Error('playwright not found (set PLAYWRIGHT_FROM to a folder whose node_modules holds it)')
  const work = await prepare(pluginPath)
  fs.mkdirSync(outDir, { recursive: true })
  const css = process.env.WPB_DESKTOP_CSS || ''
  const cssLink = css ? `<link rel="stylesheet" href="/desktop/${path.basename(css)}">` : ''
  for (const shim of ['with', 'without']) {
    const map = { imports: { react: '/harness.mjs', 'react/jsx-runtime': '/harness.mjs', '@hermes/plugin-sdk': `/sdk-${shim}.mjs` } }
    fs.writeFileSync(path.join(work, `index-${shim}.html`), `<!doctype html><html class="dark" style="${DARK}"><head><meta charset="utf-8">${cssLink}
<style>html,body{margin:0;background:var(--dt-background,#111317);color:var(--dt-foreground,#e6e8eb);font-family:var(--dt-font-sans,system-ui,sans-serif);font-size:13px}#root{max-width:1180px;padding:16px}</style>
<script type="importmap">${JSON.stringify(map)}</script></head><body><div id="root"></div>
<script type="module">import { browserCase } from '/harness.mjs'; import plugin from '/plugin.js'; const q = new URLSearchParams(location.search); browserCase(plugin, q.get('s'), '${shim}')</script></body></html>`)
  }
  const TYPES = { '.mjs': 'text/javascript', '.js': 'text/javascript', '.html': 'text/html', '.css': 'text/css', '.ttf': 'font/ttf', '.woff2': 'font/woff2', '.woff': 'font/woff' }
  const launch = { headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] }
  if (process.env.WPB_CHROMIUM) launch.executablePath = process.env.WPB_CHROMIUM
  const browser = await pw.module.chromium.launch(launch)
  try {
    for (const c of CASES) {
      const [scenario, shim] = c.split('/')
      const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 2 })
      const consoleErrors = []
      page.on('console', m => m.type() === 'error' && consoleErrors.push(m.text()))
      page.on('pageerror', e => consoleErrors.push(String(e)))
      await page.route('http://wpb.harness/**', route => {
        const url = new URL(route.request().url())
        const file = url.pathname.startsWith('/desktop/') && css
          ? path.join(path.dirname(css), path.basename(url.pathname))
          : path.join(work, path.basename(url.pathname))
        if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: 'not found' })
        return route.fulfill({ status: 200, body: fs.readFileSync(file), contentType: TYPES[path.extname(file)] || 'application/octet-stream' })
      })
      await page.goto(`http://wpb.harness/index-${shim}.html?s=${scenario}`)
      let result
      try {
        await page.waitForFunction(() => window.__WPB_RESULT !== undefined, null, { timeout: 30000 })
        result = await page.evaluate(() => window.__WPB_RESULT)
      } catch (error) {
        result = { ok: false, scenario, shim, error: 'no result: ' + String(error) }
      }
      await page.waitForTimeout(300)
      if (consoleErrors.length) {
        result.ok = false
        result.consoleErrors = consoleErrors
      }
      const shot = SHOTS[c]
      if (shot) {
        const target = page.locator(shot[1]).first()
        const png = await target.screenshot()
        const webp = await page.evaluate(async b64 => {
          const bytes = Uint8Array.from(atob(b64), ch => ch.charCodeAt(0))
          const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/png' }))
          const canvas = new OffscreenCanvas(Math.round(bmp.width / 2), Math.round(bmp.height / 2))
          canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height)
          const out = new Uint8Array(await (await canvas.convertToBlob({ type: 'image/webp', quality: 0.8 })).arrayBuffer())
          let s = ''
          for (let i = 0; i < out.length; i += 0x8000) s += String.fromCharCode(...out.subarray(i, i + 0x8000))
          return btoa(s)
        }, png.toString('base64'))
        fs.writeFileSync(path.join(outDir, shot[0] + '.webp'), Buffer.from(webp, 'base64'))
        result.shot = shot[0] + '.webp'
      }
      process.stdout.write('RESULT ' + JSON.stringify(result) + '\n')
      await page.close()
    }
  } finally {
    await browser.close()
    fs.rmSync(work, { recursive: true, force: true })
  }
}

const IS_MAIN = !IN_BROWSER && typeof process !== 'undefined' && import.meta.filename === process.argv[1]
if (IS_MAIN) {
  const [mode, ...args] = process.argv.slice(2)
  // Not awaited at the top level: case mode imports plugin-*.mjs, which imports this module again;
  // a pending top-level await here would make that import wait on itself.
  const run = mode === 'probe'
    ? loadPlaywright().then(pw => process.stdout.write(JSON.stringify({ playwright: pw ? pw.path : null }) + '\n'))
    : mode === 'case' ? caseMain(args[0], args[1])
      : mode === 'node' ? nodeMain(args[0], args.slice(1))
        : mode === 'chromium' ? chromiumMain(args[0], args[1])
          : Promise.reject(new Error('usage: probe | node <plugin.js> [case..] | chromium <plugin.js> <out dir>'))
  run.catch(error => {
    process.stderr.write(String((error && error.stack) || error) + '\n')
    process.exit(1)
  })
}
