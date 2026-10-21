// orch ui: draws what the local server reports about the state folder. It decides nothing.
// Everything reaches the page through textContent and DOM calls, never innerHTML: names and
// paths come from the user's code. It talks only to its own server.
'use strict'

// Motion: whatever is new in a view arrives once (it rises, pops or draws), numbers count to their
// value, and a poll that changed nothing moves nothing. All of it is skipped when the system asks
// for less motion.
const POLL_MS = 2000
const THEME_KEY = 'orch-ui-theme'
const ALL_KEY = 'orch-ui-all'
const EASE = 'cubic-bezier(.22, .8, .24, 1)'
const RULES = {
  O1: 'Both sessions changed the same file',
  O2: 'Both changed the same function',
  O3: 'A breaking change is used in a file the other session is changing',
  O4: 'A removed function is still used there',
  O6: 'Callers left behind that no session has updated'
}
const STRENGTHS = ['strong', 'medium', 'weak']
const AGENTS = { claude: 'Claude Code', git: 'plain git' }
const TURNS = { working: 'working', idle: 'waiting' }
const KINDS = {
  edit: 'before its next edit of the file',
  next: 'with its next tool result',
  stop: 'when it tries to finish its turn',
  wake: 'to wake it while it waits for its user'
}
const FILE_STATUS = { A: 'added', M: 'modified', D: 'deleted' }
const ICONS = {
  sun: 'M12 3v1.6M12 19.4V21M4.6 4.6l1.1 1.1M18.3 18.3l1.1 1.1M3 12h1.6M19.4 12H21M4.6 19.4l1.1-1.1M18.3 5.7l1.1-1.1M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z',
  moon: 'M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6Z',
  monitor: 'M3.5 5h17v11h-17ZM9 20h6M12 16v4',
  copy: 'M9 9h10.5v10.5H9ZM5 15V5h10',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  next: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
  close: 'M6 6l12 12M18 6 6 18',
  terminal: 'M4.5 6.5 9.5 12l-5 5.5M12 18h7.5',
  branch: 'M7 4v11M7 15a2.3 2.3 0 1 0 0 4.6A2.3 2.3 0 0 0 7 15ZM17 4.4a2.3 2.3 0 1 0 0 4.6 2.3 2.3 0 0 0 0-4.6ZM17 9c0 4.5-10 2.5-10 6',
  commit: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM3 12h6M15 12h6',
  clock: 'M12 7.5V12l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM3 20c.5-3.5 3-5.5 6-5.5s5.5 2 6 5.5M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.8c1.8.7 2.8 2.5 3 5.2',
  bolt: 'M13 3 5.5 13.5H11L10 21l8-10.5h-5.5Z',
  inbox: 'M3.5 13 6 5h12l2.5 8v6h-17ZM3.5 13h5l1.5 2.5h4L15.5 13h5',
  gauge: 'M5 18a8 8 0 1 1 14 0M12 14l3.5-4.5',
  steer: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5ZM9.8 13.4 4 16M14.2 13.4 20 16M12 9.5V3',
  bell: 'M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15ZM10 20.5a2 2 0 0 0 4 0',
  repo: 'M5 4.5h11.5a2 2 0 0 1 2 2V20H7a2 2 0 0 1-2-2ZM5 18a2 2 0 0 1 2-2h11.5',
  chevron: 'M9 6l6 6-6 6'
}

// ------------------------------------------------------------------------------- the theme
// Applied before the body is drawn: the script loads in <head>.
function stored (key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}
function store (key, value) {
  try { localStorage.setItem(key, value) } catch { /* private window: the choice lasts this visit */ }
}
function applyTheme (choice) {
  const root = document.documentElement
  if (choice === 'light' || choice === 'dark') root.dataset.theme = choice
  else delete root.dataset.theme
  for (const b of document.querySelectorAll('[data-theme-choice]')) {
    b.setAttribute('aria-pressed', String(b.dataset.themeChoice === choice))
  }
}
applyTheme(stored(THEME_KEY, 'system'))

// ------------------------------------------------------------------------------- DOM helpers
function h (tag, attrs, ...kids) {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue
    if (k === 'class') el.className = v
    else el.setAttribute(k, v === true ? '' : String(v))
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue
    el.append(kid instanceof Node ? kid : String(kid))
  }
  return el
}
function svg (tag, attrs, ...kids) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag)
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v))
  el.append(...kids.filter(Boolean))
  return el
}
function icon (name, cls) {
  return svg('svg', { viewBox: '0 0 24 24', class: 'ico' + (cls ? ' ' + cls : ''), 'aria-hidden': 'true', focusable: 'false' },
    svg('path', { d: ICONS[name] }))
}
const $ = (id) => document.getElementById(id)
const code = (text) => h('code', {}, text)
const chip = (text, cls) => h('span', { class: 'chip' + (cls ? ' ' + cls : '') }, text)
const label = (text, tag) => h(tag || 'h2', { class: 'label' }, text)
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many)

function baseName (path) {
  return String(path || '').split(/[\\/]/).filter(Boolean).pop() || String(path || '')
}
function relPath (top, path) {
  if (!path) return ''
  const p = String(path).replace(/\\/g, '/')
  const t = String(top || '').replace(/\\/g, '/').replace(/\/+$/, '')
  if (t && p.toLowerCase().startsWith(t.toLowerCase() + '/')) return p.slice(t.length + 1)
  return p
}
const symName = (s) => String(s || '').split('::').pop()
const symFile = (s) => String(s || '').split('::')[0]
const sessionHref = (id) => '#/session/' + encodeURIComponent(id)
const overlapHref = (key) => '#/overlap/' + encodeURIComponent(key)

function safeDecode (text) {
  try { return decodeURIComponent(text) } catch { return text }
}

// ------------------------------------------------------------------------------- times
function ago (ms) {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return s + ' s ago'
  if (s < 3600) return Math.floor(s / 60) + ' min ago'
  if (s < 86400) return Math.floor(s / 3600) + ' h ago'
  return Math.floor(s / 86400) + ' d ago'
}
function clock (iso, withMs) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d)) return '—'
  let text = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
  if (withMs) text += '.' + String(d.getMilliseconds()).padStart(3, '0')
  if (d.toDateString() !== new Date().toDateString()) {
    text = d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + text
  }
  return text
}
function at (iso, withMs) {
  return iso ? h('time', { datetime: iso }, clock(iso, withMs)) : '—'
}
function since (iso) {
  return iso ? h('time', { datetime: iso, 'data-since': iso }, ago(Date.now() - Date.parse(iso))) : '—'
}
function msText (ms) {
  if (ms === null || ms === undefined || !isFinite(ms)) return '—'
  if (ms < 1000) return (Math.round(ms * 10) / 10) + ' ms'
  if (ms < 60000) return (Math.round(ms / 100) / 10) + ' s'
  return Math.round(ms / 60000) + ' min'
}
function tick () {
  const now = Date.now()
  for (const el of document.querySelectorAll('time[data-since]')) el.textContent = ago(now - Date.parse(el.dataset.since))
}

// ------------------------------------------------------------------------------- motion
// An element that arrives with motion carries data-anim, a key unique in its view, and data-fx,
// how it arrives. A key plays once per visit of a view, so the redraw after a poll leaves what is
// already there alone. A number carries data-count and counts from the value it showed last.
const motion = { seen: new Set(), values: new Map(), fill: null }
const FORMATS = { int: (v) => String(Math.round(v)), ms: msText }

function calm () {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function settle (box) {
  const still = calm() || !box.animate
  let i = 0
  for (const el of box.querySelectorAll('[data-anim]')) {
    if (motion.seen.has(el.dataset.anim)) continue
    motion.seen.add(el.dataset.anim)
    if (!still) play(el, el.dataset.fx || 'rise', Math.min(i++ * 45, 560))
  }
  for (const el of box.querySelectorAll('[data-count]')) count(el, still)
}

function play (el, fx, delay) {
  const timing = { duration: 640, delay, easing: EASE, fill: 'backwards' }
  if (fx === 'rise') {
    el.animate([{ opacity: 0, transform: 'translateY(14px)', filter: 'blur(6px)' },
      { opacity: 1, transform: 'none', filter: 'blur(0)' }], timing)
  } else if (fx === 'pop') {
    el.animate([{ opacity: 0, transform: 'scale(.55)' }, { opacity: 1, transform: 'none' }], { ...timing, duration: 520, delay: delay + 120 })
  } else if (fx === 'draw') {
    el.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { ...timing, duration: 760, delay: delay + 260 })
  } else if (fx === 'grow') {
    el.animate([{ transform: 'scaleY(0)' }, { transform: 'scaleY(1)' }], { ...timing, duration: 760 })
  } else if (fx === 'words') {
    let j = 0
    for (const w of el.querySelectorAll('.w')) {
      w.animate([{ opacity: 0, transform: 'translateY(8px)', filter: 'blur(10px)' },
        { opacity: 1, transform: 'none', filter: 'blur(0)' }], { ...timing, duration: 600, delay: delay + j++ * 70 })
    }
  }
}

function count (el, still) {
  const key = el.dataset.count
  const to = Number(el.dataset.value)
  const format = FORMATS[el.dataset.format] || FORMATS.int
  if (!isFinite(to)) {
    motion.values.delete(key)
    return
  }
  const from = motion.values.has(key) ? motion.values.get(key) : 0
  motion.values.set(key, to)
  if (still || from === to) return
  const start = performance.now()
  const frame = (now) => {
    const k = Math.min(1, (now - start) / 900)
    el.textContent = format(from + (to - from) * (1 - Math.pow(1 - k, 3)))
    if (k < 1 && el.isConnected) requestAnimationFrame(frame)
  }
  el.textContent = format(from)
  requestAnimationFrame(frame)
}

function num (key, value, format, cls) {
  const v = value === null || value === undefined ? NaN : Number(value)
  return h('span', { class: cls, 'data-count': key, 'data-value': String(v), 'data-format': format },
    isFinite(v) ? FORMATS[format](v) : '—')
}

function title (text, key) {
  const words = String(text).split(/(\s+)/).filter(Boolean).map((part) => (/^\s+$/.test(part) ? part : h('span', { class: 'w' }, part)))
  return h('h1', { 'data-anim': 'title:' + key, 'data-fx': 'words' }, words)
}

// ------------------------------------------------------------------------------- the server
class AccessError extends Error {}
const cache = new Map()            // url -> { etag, data }

async function load (url) {
  const known = cache.get(url)
  const headers = known && known.etag ? { 'If-None-Match': known.etag } : {}
  const res = await fetch(url, { headers, cache: 'no-store', credentials: 'same-origin' })
  if (res.status === 304 && known) return { data: known.data, changed: false }
  if (res.status === 403) throw new AccessError()
  if (res.status === 404) {
    const changed = !known || known.data !== null
    cache.set(url, { etag: null, data: null })
    return { data: null, changed }
  }
  if (!res.ok) throw new Error('HTTP ' + res.status)
  const data = await res.json()
  cache.set(url, { etag: res.headers.get('ETag'), data })
  return { data, changed: true }
}

async function post (url, body) {
  const res = await fetch(url, {
    method: 'POST', cache: 'no-store', credentials: 'same-origin', body: JSON.stringify(body || {}),
    headers: { 'Content-Type': 'application/json', 'X-Orch-UI': '1' }
  })
  if (res.status === 403) throw new AccessError()
  let data = null
  try { data = await res.json() } catch { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || 'HTTP ' + res.status)
  return data
}

// ------------------------------------------------------------------------------- state and routes
const ui = {
  route: null, status: null, detail: null, health: null, push: null, notes: null, demo: null,
  showAll: stored(ALL_KEY, '0') === '1', lastOk: null, lost: false, denied: false, dirty: true,
  switchBusy: false, switchResult: null, demoBusy: false, demoError: null, sheetStep: null, sheetOpen: false
}

function parseRoute () {
  const raw = location.hash.replace(/^#/, '') || '/'
  const cut = raw.indexOf('?')
  const path = cut < 0 ? raw : raw.slice(0, cut)
  const params = new URLSearchParams(cut < 0 ? '' : raw.slice(cut + 1))
  const parts = path.split('/').filter(Boolean)
  const rest = safeDecode(parts.slice(1).join('/'))
  if (parts[0] === 'session' && rest) return { name: 'session', id: rest, params }
  if (parts[0] === 'overlap' && rest) return { name: 'overlap', key: rest, params }
  if (parts[0] === 'notes') return { name: 'notes', params }
  if (parts[0] === 'settings') return { name: 'settings', params }
  return { name: 'overview', params }
}

function notesQuery (params) {
  const q = new URLSearchParams()
  for (const k of ['session', 'kind', 'state']) if (params.get(k)) q.set(k, params.get(k))
  return q.toString()
}

function urlsFor (route, demo) {
  const want = {}
  if (demo) want.demo = '/api/demo'
  if (route.name === 'overview') { want.push = '/api/push'; want.notes = '/api/notes?' }
  if (route.name === 'session') want.detail = '/api/sessions/' + encodeURIComponent(route.id)
  if (route.name === 'overlap') want.detail = '/api/findings/' + encodeURIComponent(route.key)
  if (route.name === 'notes') want.detail = '/api/notes?' + notesQuery(route.params)
  if (route.name === 'settings') { want.detail = '/api/config'; want.health = '/api/health' }
  return want
}

let timer = null
let generation = 0
let skeletonTimer = null

async function poll () {
  clearTimeout(timer)
  if (ui.denied) return
  const gen = ++generation
  const route = parseRoute()
  try {
    const status = await load('/api/status?ended=' + (ui.showAll ? 1 : 0))
    const want = urlsFor(route, status.data && status.data.ui && status.data.ui.demo)
    const got = await Promise.all(Object.entries(want).map(async ([k, url]) => [k, await load(url)]))
    if (gen !== generation) return                     // a newer poll took over (the route changed)
    const changed = ui.dirty || status.changed || got.some(([, r]) => r.changed)
    ui.route = route
    ui.status = status.data
    ui.detail = ui.health = ui.push = ui.notes = ui.demo = null
    for (const [k, r] of got) ui[k] = r.data
    ui.lastOk = new Date()
    ui.lost = false
    ui.dirty = false
    if (changed) render()
  } catch (err) {
    if (err instanceof AccessError) return denied()
    if (gen !== generation) return
    ui.lost = true
  }
  drawHeader()
  timer = setTimeout(poll, document.hidden ? POLL_MS * 5 : POLL_MS)
}

function refresh () {
  ui.dirty = true
  poll()
}

async function navigate () {
  const route = parseRoute()
  const main = $('main')
  if (ui.route && ui.route.name === 'notes' && route.name === 'notes') return refresh()   // a filter: same view
  clearTimeout(timer)
  const gen = ++generation                             // a poll on its way belongs to the old view
  ui.dirty = true
  motion.seen.clear()
  motion.values.clear()
  markTabs(route)
  main.focus({ preventScroll: true })
  if (!calm() && main.firstChild && main.animate) {
    await main.animate([{ opacity: 1 }, { opacity: 0, transform: 'translateY(-6px)' }],
      { duration: 160, easing: 'ease-in', fill: 'forwards' }).finished.catch(() => {})
  }
  if (gen !== generation) return                       // drawn already, or another link was followed
  window.scrollTo(0, 0)
  clearTimeout(skeletonTimer)
  skeletonTimer = setTimeout(() => skeleton(route), 240)   // only when the answer is slow
  poll()
}

// ------------------------------------------------------------------------------- drawing
function swap (box, nodes) {
  // Keep the keyboard where it was across redraws: focus and open <details> are restored by key.
  const active = document.activeElement
  const keyOf = (el) => el.dataset.key || el.getAttribute('href') || el.id || null
  const focus = active && box.contains(active) && active !== box ? keyOf(active) : null
  const open = [...box.querySelectorAll('details[open][data-key]')].map((d) => d.dataset.key)
  box.replaceChildren(...nodes)
  for (const d of box.querySelectorAll('details[data-key]')) if (open.includes(d.dataset.key)) d.open = true
  if (focus) {
    const again = [...box.querySelectorAll('[data-key], [href], [id]')].find((el) => keyOf(el) === focus)
    if (again) again.focus({ preventScroll: true })
  }
}

function render () {
  clearTimeout(skeletonTimer)
  const main = $('main')
  for (const a of main.getAnimations()) a.cancel()     // the fade of the view it replaces
  const views = { overview, session: sessionView, overlap: overlapView, notes: notesView, settings: settingsView }
  swap(main, views[ui.route.name]())
  settle(main)
  markTabs(ui.route)
  drawDock()
  drawSheet()
  tick()
}

function skeleton (route) {
  const main = $('main')
  for (const a of main.getAnimations()) a.cancel()
  const line = () => h('div', { class: 'skel line' })
  const tiles = (n) => h('div', { class: 'kpis' }, Array.from({ length: n }, () => h('div', { class: 'skel' })))
  const blocks = {
    overview: [line(), tiles(4), h('div', { class: 'cards' }, [0, 1, 2].map(() => h('div', { class: 'skel tall' })))],
    notes: [line(), tiles(4), h('div', { class: 'skel tall' })]
  }
  main.replaceChildren(...(blocks[route.name] ||
    [line(), h('div', { class: 'skel' }), h('div', { class: 'split' }, h('div', { class: 'skel tall' }), h('div', { class: 'skel tall' }))]))
}

function markTabs (route) {
  for (const a of document.querySelectorAll('.tabs a')) {
    const current = a.dataset.route === route.name || (a.dataset.route === 'overview' && ['session', 'overlap'].includes(route.name))
    if (current) a.setAttribute('aria-current', 'page')
    else a.removeAttribute('aria-current')
  }
  moveIndicator()
}

function moveIndicator () {
  // The pill behind the current tab slides to it; it is placed without motion the first time.
  // The header's height is kept too: the sheet opens below it.
  document.documentElement.style.setProperty('--band-h', document.querySelector('.band').offsetHeight + 'px')
  const ind = document.querySelector('.tab-ind')
  const a = document.querySelector('.tabs a[aria-current="page"]')
  if (!ind || !a) return
  ind.style.width = a.offsetWidth + 'px'
  ind.style.transform = 'translateX(' + a.offsetLeft + 'px)'
  if (!ind.classList.contains('ready')) requestAnimationFrame(() => ind.classList.add('ready'))
}

function drawHeader () {
  const st = ui.status
  const coord = $('coord')
  const text = $('coord-text')
  const c = st && st.coordinator
  if (c && c.running) {
    coord.classList.remove('down')
    text.textContent = 'Coordinator running since ' + clock(c.since) + ', version ' + (c.version || '?')
  } else if (c) {
    coord.classList.add('down')
    text.textContent = 'Coordinator not running: it starts with the next hook event'
  }
  coord.title = text.textContent
  const updated = $('updated')
  updated.classList.toggle('lost', ui.lost)
  if (ui.lost) updated.textContent = 'Cannot reach orch ui' + (ui.lastOk ? ' since ' + clock(ui.lastOk.toISOString()) : '')
  else if (ui.lastOk) updated.textContent = 'Updated ' + clock(ui.lastOk.toISOString())
  $('demo-badge').hidden = !(st && st.ui && st.ui.demo)
}

function denied () {
  ui.denied = true
  clearTimeout(timer)
  clearTimeout(skeletonTimer)
  $('demo').hidden = true
  $('sheet').hidden = true
  document.body.classList.remove('has-dock')
  const main = $('main')
  for (const a of main.getAnimations()) a.cancel()
  swap(main, [h('div', { class: 'notice', role: 'alert' },
    h('strong', {}, 'This page can no longer reach orch ui. '),
    'Open the link that orch ui printed in your terminal: it changes each time orch ui starts.')])
  const updated = $('updated')
  updated.classList.add('lost')
  updated.textContent = 'No access'
}

function announce (text) {
  const el = $('announce')
  el.textContent = ''
  requestAnimationFrame(() => { el.textContent = text })
}

// ------------------------------------------------------------------------------- shared pieces
function agentName (agent) {
  return AGENTS[agent] || agent || 'agent'
}

function sessionsOf () {
  // Every session the status knows, by id, to name the ones a finding or a note mentions.
  const out = {}
  for (const repo of (ui.status && ui.status.repositories) || []) for (const s of repo.sessions) out[s.id] = s
  return out
}

function whoText (sessions, id) {
  const s = sessions[id]
  if (!s) return String(id || '').slice(0, 8) || '?'
  return baseName(s.worktree) + (s.agent === 'git' ? ' (plain git)' : '')
}
function whoLink (sessions, id) {
  return h('a', { href: sessionHref(id) }, whoText(sessions, id))
}

function initials (s) {
  const name = String((s && (baseName(s.worktree) || s.id)) || '?')
  const parts = name.split(/[-_. ]+/).filter(Boolean)
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : name.slice(0, 2)).toUpperCase()
}
function avatar (s, size, key) {
  return h('span', { class: 'av' + (size ? ' ' + size : '') + (s && s.agent === 'git' ? ' git' : ''), 'aria-hidden': 'true',
    'data-anim': key || null, 'data-fx': key ? 'pop' : null }, initials(s))
}

function stateChip (s) {
  if (s.state !== 'live') return chip(s.note || 'ended', 'quiet')
  if (s.agent === 'git') return chip('live · plain git', 'live quiet')
  return chip('live · ' + (TURNS[s.turn] || 'idle'), 'live ' + (s.turn === 'working' ? 'working' : 'quiet'))
}

function shortBase (s) {
  return (s.base_commit ? s.base_commit.slice(0, 9) : '—') + (s.base_guessed ? '?' : '')
}

function copyButton (text) {
  const btn = h('button', { type: 'button', class: 'btn icon', 'data-key': 'copy:' + text, 'aria-label': 'Copy ' + text, title: 'Copy' },
    icon('copy'))
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text)
      btn.replaceChildren(icon('check'))
      btn.classList.add('done')
      announce('Copied')
      setTimeout(() => { btn.replaceChildren(icon('copy')); btn.classList.remove('done') }, 1600)
    } catch {
      const target = btn.previousElementSibling
      if (target) window.getSelection().selectAllChildren(target)
      announce('Selected: press Ctrl+C to copy')
    }
  })
  return btn
}
function command (text) {
  return h('div', { class: 'cmd' }, code(text), copyButton(text))
}

function emptyState (heading, body, key, extra) {
  const mark = key === 'repos' && svg('svg', { viewBox: '0 0 96 96', class: 'mark-big', 'aria-hidden': 'true' },
    svg('path', { d: 'M19 18 L50.2 36 M19 78 L50.2 60', fill: 'none', 'stroke-width': '10', 'stroke-linecap': 'round' }),
    svg('circle', { cx: '71', cy: '48', r: '11' }))
  return h('div', { class: 'empty', 'data-anim': 'empty:' + key }, mark, h('h3', {}, heading), h('p', {}, body), extra)
}
function backLink () {
  return h('a', { class: 'back', href: '#/' }, icon('back'), 'Overview')
}
function notFound (what) {
  return [backLink(), emptyState(what + ' not found',
    'The record has nothing under this name. It may come from an earlier state folder.', 'missing')]
}

function terminal (heading, text, key) {
  // What a hook or git prints, as the agent or the terminal gets it.
  return h('div', { class: 'term', 'data-anim': key || null },
    h('div', { class: 'term-bar' }, h('span', { class: 'lights', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), heading),
    h('pre', {}, text))
}

function connector (key, both) {
  return svg('svg', { class: 'conn', viewBox: '0 0 54 16', 'aria-hidden': 'true' },
    svg('path', { d: 'M3 8H50', pathLength: '1', 'stroke-dasharray': '1', 'data-anim': key, 'data-fx': 'draw' }),
    svg('path', { d: 'M44 3.5 50 8l-6 4.5' }),
    both && svg('path', { d: 'M9 3.5 3 8l6 4.5' }))
}

function overlapWhat (o) {
  const name = code(symName(o.symbol))
  const where = code(symFile(o.symbol))
  if (o.rule === 'O1') return [code(o.file)]
  if (o.rule === 'O2') return [name, ' in ', where]
  if (o.rule === 'O3') return [name, ' in ', where, ' → ', code(o.file)]
  if (o.rule === 'O4') return [name, ' removed, still used in ', code(o.file)]
  return [name, ' in ', where, ' · ', plural(((o.detail || {}).left || []).length, 'caller', 'callers'), ' left behind']
}

function overlapNames (sessions, o) {
  if (o.rule === 'O6') return whoText(sessions, o.changer) + ' → callers no session updated'
  if (o.rule === 'O3' || o.rule === 'O4') return whoText(sessions, o.changer) + ' → ' + whoText(sessions, o.receiver)
  return (o.sessions || []).map((id) => whoText(sessions, id)).join(' ↔ ')
}

function overlapRow (sessions, o) {
  const key = 'ov:' + o.key
  let flow
  if (o.rule === 'O3' || o.rule === 'O4') {
    flow = [avatar(sessions[o.changer], 'sm'), connector(key + ':line'), avatar(sessions[o.receiver], 'sm')]
  } else if (o.rule === 'O6') {
    flow = [avatar(sessions[o.changer], 'sm'), connector(key + ':line'), h('span', { class: 'callers' }, String(((o.detail || {}).left || []).length))]
  } else {
    const [a, b] = o.sessions || []
    flow = [avatar(sessions[a], 'sm'), connector(key + ':line', true), avatar(sessions[b], 'sm')]
  }
  const names = overlapNames(sessions, o)
  return h('li', { class: 'card lifts ov ' + o.strength, 'data-anim': key },
    h('div', { class: 'ov-flow', title: names }, flow),
    h('div', { class: 'ov-body' }, h('a', { class: 'ov-title', href: overlapHref(o.key) }, RULES[o.rule] || o.rule),
      h('p', { class: 'ov-what' }, h('span', { class: 'sr-only' }, names + ': '), overlapWhat(o))),
    h('div', { class: 'ov-side' }, o.detail && o.detail.latent ? chip('latent', 'warn') : null, chip(o.rule, 'quiet mono'),
      chip(o.strength, o.strength)))
}

function noteState (n) {
  return chip(n.state, n.state === 'pending' || n.state === 'withdrawn' ? 'quiet' : '')
}
function deliveredText (n) {
  if (n.delivered) return at(n.delivered, true)
  if (n.state === 'delivered') return n.heard_with ? 'with its ' + n.heard_with + ' note' : 'yes'
  return '—'
}
function noteWhen (n) {
  return n.kind === 'edit' && n.file ? 'before its next edit of ' + n.file : KINDS[n.kind] || n.kind
}
function noteAbout (n) {
  return [h('a', { href: overlapHref(n.finding_key) }, n.rule), ' ', code(symName(n.symbol || n.file)),
    (n.rule === 'O3' || n.rule === 'O4') && n.file ? [' → ', code(n.file)] : null]
}
function noteText (n) {
  return h('details', { 'data-key': 'note-' + n.id }, h('summary', { 'data-key': 'show-' + n.id }, icon('chevron'), 'Text'),
    terminal('what the agent reads', n.text))
}

function noteCard (n, sessions, to) {
  const stops = [['Created', at(n.created, true), true], ['Written', at(n.written, true), !!n.written],
    ['Delivered', deliveredText(n), n.state === 'delivered']]
  return h('article', { class: 'card note', 'data-anim': 'note:' + n.id },
    h('div', { class: 'note-head' }, chip(n.kind, 'mono'), noteState(n),
      h('span', { class: 'about' }, to ? [avatar(sessions[n.session_id], 'sm'), whoLink(sessions, n.session_id), ' · '] : null,
        noteWhen(n), ' · ', noteAbout(n))),
    h('dl', { class: 'journey' },
      stops.map(([name, value, on]) => h('div', { class: 'stop' + (on ? ' on' : '') }, h('dt', {}, name), h('dd', {}, value))),
      h('div', { class: 'tta' }, h('dt', {}, 'time to awareness'), h('dd', {}, num('tta:' + n.id, n.time_to_awareness_ms, 'ms', 'v')))),
    noteText(n))
}

function fact (name, value) {
  return h('div', {}, h('dt', {}, name), h('dd', {}, value))
}

function kpi (key, name, ic, value, format, sub, accent) {
  return h('div', { class: 'card kpi' + (accent ? ' accent' : ''), 'data-anim': 'kpi:' + key },
    icon(ic, 'big'), h('p', { class: 'label' }, name), num('kpi:' + key, value, format, 'num'), h('p', { class: 'sub' }, sub))
}

// ------------------------------------------------------------------------------- the overview
function overview () {
  const repos = (ui.status && ui.status.repositories) || []
  const out = [h('div', { class: 'hero' }, h('div', { class: 'hero-main' }, title('Overview', 'overview')),
    repos.length ? showAllSwitch() : null)]
  if (!repos.length) {
    out.push(emptyState('No watched repository',
      ['Run ', code('orch init'), ' in your repository: it installs the hooks and watches the repository it runs in.'],
      'repos', command('orch init')))
    return out
  }
  out.push(overviewKpis(repos))
  const pushes = ui.push ? ui.push.push : []
  const placed = new Set()
  repos.forEach((repo, i) => {
    const ids = new Set(repo.sessions.map((s) => s.id))
    const mine = pushes.filter((p) => p.sessions.some((id) => ids.has(id)))
    mine.forEach((p) => placed.add(p.name))
    out.push(repoSection(repo, i, mine))
  })
  const others = pushes.filter((p) => !placed.has(p.name))
  if (others.length) out.push(h('section', { class: 'section' }, label('Other push lines'), pushList(others)))
  return out
}

function showAllSwitch () {
  const btn = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(ui.showAll), 'data-key': 'show-all',
    'aria-labelledby': 'show-all-label' })
  btn.addEventListener('click', () => {
    ui.showAll = !ui.showAll
    store(ALL_KEY, ui.showAll ? '1' : '0')
    refresh()
  })
  return h('div', { class: 'chips' }, h('span', { id: 'show-all-label', class: 'muted' }, 'Ended sessions and latent overlaps'), btn)
}

function overviewKpis (repos) {
  const live = repos.reduce((n, r) => n + r.sessions.filter((s) => s.state === 'live').length, 0)
  const overlaps = repos.flatMap((r) => r.overlaps)
  const strong = overlaps.filter((o) => o.strength === 'strong').length
  const others = overlaps.length - strong
  const notes = ui.notes ? ui.notes.notes : []
  const waiting = notes.filter((n) => n.state === 'pending' || n.state === 'written').length
  const m = ui.notes ? ui.notes.summary : null
  return h('section', { class: 'kpis', 'aria-label': 'At a glance' },
    kpi('live', 'Live sessions', 'users', live, 'int', 'in ' + plural(repos.length, 'repository', 'repositories')),
    kpi('strong', 'Strong overlaps', 'bolt', strong, 'int', others ? plural(others, 'other overlap', 'other overlaps') : 'no other overlap',
      strong > 0),
    kpi('waiting', 'Notes waiting', 'inbox', waiting, 'int', m ? m.delivered + ' delivered' : '—'),
    kpi('median', 'Time to awareness', 'gauge', m && m.median_ms, 'ms', m && m.measured ? 'median of ' + m.measured : 'no note delivered yet'))
}

function repoSection (repo, i, pushes) {
  const sessions = Object.assign(sessionsOf(), Object.fromEntries(repo.sessions.map((s) => [s.id, s])))
  return h('section', { class: 'section', 'aria-labelledby': 'repo-' + i },
    h('div', { class: 'section-head', 'data-anim': 'repo:' + repo.common_dir },
      h('h2', { id: 'repo-' + i }, repo.name), h('span', { class: 'muted mono', title: repo.common_dir }, repo.common_dir)),
    repo.sessions.length
      ? h('ul', { class: 'cards', 'aria-label': 'Sessions' }, repo.sessions.map(sessionCard))
      : emptyState('No live sessions', 'A session shows here as soon as an agent with the hooks starts in this repository, ' +
        'or a worktree of it changes.', 'sessions:' + i),
    repo.overlaps.length ? overlapGroups(repo, sessions)
      : emptyState('No overlaps', 'What these sessions changed does not touch.', 'overlaps:' + i),
    pushes.length ? pushList(pushes) : null)
}

function sessionCard (s) {
  const files = Object.entries(s.changes.files || {})
  const unattributed = s.changes.unattributed || []
  const symbols = s.changes.symbols || []
  const breaking = symbols.filter((c) => c.contract === 'breaking').length
  const review = symbols.filter((c) => c.contract === 'review').length
  const shown = files.slice(0, 6)
  const metric = (key, n, word, cls) => h('div', { class: 'metric' + (n && cls ? ' ' + cls : '') },
    num('s:' + s.id + ':' + key, n, 'int', 'n'), h('span', { class: 't' }, word))
  return h('li', { class: 'card lifts session', 'data-anim': 's:' + s.id },
    h('div', { class: 's-head' }, avatar(s, '', 's:' + s.id + ':av'),
      h('div', { class: 's-who' }, h('a', { class: 's-name', href: sessionHref(s.id) }, baseName(s.worktree) || s.id),
        h('span', { class: 's-agent' }, agentName(s.agent)))),
    h('div', { class: 's-meta' }, h('span', {}, icon('branch'), s.branch || '(detached)'),
      h('span', { class: 'mono', title: 'base commit' + (s.base_guessed ? ', guessed: first seen after it started' : '') },
        icon('commit'), shortBase(s))),
    h('div', { class: 'metrics', role: 'group', 'aria-label': 'What it changed' },
      metric('files', files.length, files.length === 1 ? 'file' : 'files'),
      metric('breaking', breaking, 'breaking', 'breaking'), metric('review', review, 'review', 'review')),
    files.length || unattributed.length
      ? h('ul', { class: 'strip', 'aria-label': 'Files changed' },
        shown.map(([path, st]) => h('li', { title: path + ': ' + (FILE_STATUS[st] || st) }, h('span', { class: 'st' }, st), baseName(path))),
        files.length > shown.length ? h('li', { class: 'more' }, '+' + (files.length - shown.length)) : null,
        unattributed.length ? h('li', { class: 'more', title: 'changed in a folder another live session shares' },
          unattributed.length + ' not attributed') : null)
      : null,
    h('div', { class: 's-foot' }, h('span', {}, icon('clock'), since(s.last_event)), stateChip(s)))
}

function overlapGroups (repo, sessions) {
  const titles = { strong: 'Strong', medium: 'Medium', weak: 'Weak' }
  return h('div', { class: 'groups' }, STRENGTHS.map((k) => {
    const list = repo.overlaps.filter((o) => o.strength === k)
    if (!list.length) return null
    return h('section', { class: 'section' },
      h('div', { class: 'group-title', 'data-anim': 'g:' + repo.common_dir + ':' + k },
        h('h3', { class: 'label' }, titles[k] + ' overlaps'), chip(String(list.length), k)),
      h('ul', { class: 'ov-list' }, list.map((o) => overlapRow(sessions, o))))
  }))
}

function pushList (pushes) {
  return h('div', { class: 'split' }, pushes.map((p) => terminal(
    ['git push', p.worktree ? ' · ' + baseName(p.worktree) : '', ' · ', since(p.modified)], p.line, 'push:' + p.name)))
}

// ------------------------------------------------------------------------------- a session
function sessionView () {
  const d = ui.detail
  if (!d) return notFound('Session')
  const s = d.session
  const sessions = Object.assign(sessionsOf(), d.sessions || {})
  const files = Object.entries(d.changes.files || {})
  const unattributed = d.changes.unattributed || []
  const symbols = d.changes.symbols || []
  const out = [backLink(),
    h('div', { class: 'hero' },
      h('div', { class: 'hero-id' }, avatar(s, 'lg', 'hero-av'),
        h('div', { class: 'hero-main' }, h('p', { class: 'label' }, 'Session · ' + agentName(s.agent)),
          title(baseName(s.worktree) || s.id, 'session:' + s.id))),
      h('div', { class: 'chips', 'data-anim': 'hero-chips' }, stateChip(s), chip(s.branch || '(detached)', 'mono'),
        chip('base ' + shortBase(s), 'mono'), h('span', { class: 'chip quiet' }, icon('clock'), since(s.last_event))))]
  out.push(h('section', { class: 'card', 'data-anim': 'facts' }, h('dl', { class: 'facts' },
    fact('Session id', code(s.id)), fact('Repository', s.repository.name || '—'), fact('Worktree', code(s.worktree || '—')),
    fact('Base commit', [code(s.base_commit || '—'), s.base_guessed ? ' (guessed: first seen after it started)' : '']),
    fact('Started', at(s.started)), fact('Last event', at(s.last_event)),
    fact('State', s.state === 'live' ? (s.agent === 'git' ? 'live, plain git: no hooks' : 'live, ' +
      (s.turn === 'working' ? 'working on a turn' : s.turn === 'idle' ? 'waiting for its user' : 'idle')) : (s.note || 'ended')))))
  out.push(h('div', { class: 'split' },
    h('section', { class: 'card', 'data-anim': 'files' },
      h('div', { class: 'card-head' }, label('Files changed'), chip(String(files.length + unattributed.length), 'quiet')),
      files.length || unattributed.length ? h('div', { class: 'table-wrap' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Path'), h('th', {}, 'Status'), h('th', {}, 'This session’s'))),
        h('tbody', {},
          files.map(([p, st]) => h('tr', {}, h('td', {}, code(p)), h('td', { class: 'nowrap' }, chip(FILE_STATUS[st] || st, 'mono')),
            h('td', {}, 'yes'))),
          unattributed.map((p) => h('tr', {}, h('td', {}, code(p)), h('td', {}, '—'),
            h('td', {}, 'not attributed: changed in a folder another live session shares'))))))
        : h('p', { class: 'muted' }, 'Nothing changed since its base commit.')),
    h('section', { class: 'card', 'data-anim': 'symbols' },
      h('div', { class: 'card-head' }, label('Functions and classes'), chip(String(symbols.length), 'quiet')),
      symbols.length ? h('div', { class: 'table-wrap' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Symbol'), h('th', {}, 'Change'), h('th', {}, 'Contract'))),
        h('tbody', {}, symbols.map((c) => h('tr', {},
          h('td', {}, code(symName(c.symbol)), h('br'), h('span', { class: 'muted' }, symFile(c.symbol)),
            c.moved_from ? [h('br'), h('span', { class: 'muted' }, 'from ', code(c.moved_from))] : null),
          h('td', {}, c.kind, c.change ? ' · ' + c.change : ''),
          h('td', {}, chip(c.contract || 'none', c.contract || 'none'),
            c.reasons && c.reasons.length ? h('ul', { class: 'reasons' }, c.reasons.map((r) => h('li', {}, r))) : null))))))
        : h('p', { class: 'muted' }, 'No Python function or class changed.'))))
  out.push(noteSection('Notes to this session', d.notes_received, sessions, false, 'received',
    'Nothing another session did has concerned this one so far.'))
  out.push(noteSection('Notes its changes caused for other sessions', d.notes_caused, sessions, true, 'caused',
    'No other session has been told about this one’s changes.'))
  if (d.push) out.push(terminal('git push · this worktree · it never blocks a push', d.push.line, 'push'))
  out.push(h('section', { class: 'card', 'data-anim': 'events' },
    h('div', { class: 'card-head' }, label('Events, latest first'), chip(String(d.events.length), 'quiet')),
    d.events.length ? h('ol', { class: 'tl' }, d.events.map((e) => h('li', { class: e.took ? 'took' : '' },
      at(e.ts, true), h('span', { class: 'ev' }, e.event),
      h('span', { class: 'what' },
        e.tool_name || '', e.file_path ? [' ', code(relPath(s.worktree, e.file_path))] : '',
        e.took ? (e.tool_name || e.file_path ? ' · ' : '') + 'took its ' + e.took + ' note' : '',
        e.source ? 'source: ' + e.source : '', e.reason ? 'reason: ' + e.reason : ''))))
      : h('p', { class: 'muted' }, 'No events recorded.')))
  return out
}

function noteSection (name, notes, sessions, to, key, none) {
  return h('section', { class: 'section' },
    h('div', { class: 'section-head', 'data-anim': 'nh:' + key }, label(name), chip(String(notes.length), 'quiet')),
    notes.length ? h('div', { class: 'notes' }, notes.map((n) => noteCard(n, sessions, to))) : emptyState('No notes', none, 'notes:' + key))
}

// ------------------------------------------------------------------------------- an overlap
function overlapView () {
  const d = ui.detail
  if (!d) return notFound('Overlap')
  const f = d.finding
  const sessions = Object.assign(sessionsOf(), d.sessions || {})
  const det = f.detail || {}
  const out = [backLink(),
    h('div', { class: 'hero' },
      h('div', { class: 'hero-main' }, h('p', { class: 'label' }, 'Overlap · ' + f.rule), title(RULES[f.rule] || f.rule, 'overlap:' + f.key)),
      h('div', { class: 'chips', 'data-anim': 'hero-chips' }, chip(f.strength, f.strength), chip(f.state, f.state === 'open' ? '' : 'quiet'),
        f.latent ? chip('latent', 'warn') : null))]
  out.push(h('section', { class: 'card', 'data-anim': 'picture' }, picture(f, sessions)))
  out.push(h('section', { class: 'card', 'data-anim': 'finding' }, h('dl', { class: 'facts' },
    f.symbol ? fact('Function', [code(symName(f.symbol)), ' in ', code(symFile(f.symbol))]) : null,
    fact('File', code(f.file || '—')),
    det.moved_to ? fact('Moved to', code(det.moved_to)) : null,
    fact('Reasons', det.reasons && det.reasons.length ? h('ul', { class: 'reasons' }, det.reasons.map((r) => h('li', {}, r))) : '—'),
    fact('Latent', f.latent ? 'yes: the receiving session hears before its first edit of the file' : 'no'),
    fact('First seen', at(f.first_seen)), fact('Last seen', at(f.last_seen)),
    f.resolved ? fact('Resolved', at(f.resolved)) : null)))
  if (f.rule === 'O6' || f.rule === 'O3' || f.rule === 'O4') {
    const rows = f.rule === 'O6' ? (det.left || []).map((c) => [c.symbol, c.file || symFile(c.symbol), c.lines])
      : (det.uses || []).map((u) => [u.symbol, symFile(u.symbol), u.lines])
    out.push(h('section', { class: 'card', 'data-anim': 'uses' },
      h('div', { class: 'card-head' }, label(f.rule === 'O6' ? 'Callers left behind' : 'Uses'), chip(String(rows.length), 'quiet')),
      rows.length ? h('div', { class: 'table-wrap' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Function'), h('th', {}, 'File'), h('th', {}, 'Lines'))),
        h('tbody', {}, rows.map(([sym, file, lines]) => h('tr', {}, h('td', {}, code(symName(sym))), h('td', {}, code(file)),
          h('td', { class: 'num' }, (lines || []).join(', ') || '—'))))))
        : h('p', { class: 'muted' }, 'None listed.')))
  }
  if (f.rule === 'O2' && det.kinds) {
    out.push(h('section', { class: 'card', 'data-anim': 'kinds' }, h('dl', { class: 'facts' },
      Object.entries(det.kinds).map(([sid, kind]) => fact(whoText(sessions, sid), kind)))))
  }
  out.push(h('section', { class: 'card', 'data-anim': 'notes' },
    h('div', { class: 'card-head' }, label('Notes made for it'), chip(String(f.notes.length), 'quiet')),
    f.notes.length ? noteTable(f.notes, sessions, false)
      : h('p', { class: 'muted' }, f.rule === 'O1' ? 'None: the same file in two sessions only shows here.' : 'None made.')))
  return out
}

function picture (f, sessions) {
  const node = (id, role, key) => {
    const s = sessions[id] || {}
    return h('div', { class: 'node' }, avatar(sessions[id], '', 'pic:' + key),
      h('div', { class: 's-who' }, h('span', { class: 's-agent' }, role), whoLink(sessions, id),
        h('span', { class: 'muted' }, agentName(s.agent) + (s.branch ? ' · ' + s.branch : ''))))
  }
  const mid = (text, both) => h('div', { class: 'link-mid' }, h('span', {}, text), connector('pic:line', both))
  const name = symName(f.symbol)
  if (f.rule === 'O3' || f.rule === 'O4') {
    return h('div', { class: 'flow-big ' + f.strength }, node(f.changer, 'changing', 'a'),
      mid((f.rule === 'O4' ? 'removed ' : 'breaking change to ') + name), node(f.receiver, 'receiving', 'b'))
  }
  if (f.rule === 'O6') {
    const left = (f.detail.left || []).length
    return h('div', { class: 'flow-big ' + f.strength }, node(f.changer, 'changing', 'a'), mid('breaking change to ' + name),
      h('div', { class: 'node none' }, h('span', { class: 'av none', 'aria-hidden': 'true' }, String(left)),
        h('div', { class: 's-who' }, h('span', { class: 's-agent' }, 'left behind'), h('strong', {}, plural(left, 'caller', 'callers')),
          h('span', { class: 'muted' }, 'that no session has updated'))))
  }
  const [a, b] = f.sessions
  return h('div', { class: 'flow-big ' + f.strength }, node(a, 'session', 'a'), mid('both changed ' + (name || f.file), true),
    node(b, 'session', 'b'))
}

function noteTable (notes, sessions, withAbout) {
  return h('div', { class: 'table-wrap' }, h('table', {},
    h('caption', { class: 'sr-only' }, 'Notes'),
    h('thead', {}, h('tr', {}, ['Created', 'To', 'Kind', 'State', withAbout ? 'About' : null, 'Delivered', 'Time to awareness', 'Text']
      .filter(Boolean).map((t) => h('th', {}, t)))),
    h('tbody', {}, notes.map((n) => h('tr', {},
      h('td', { class: 'num nowrap' }, at(n.created, true)),
      h('td', { class: 'nowrap' }, h('span', { class: 'who-cell' }, avatar(sessions[n.session_id], 'sm'), whoLink(sessions, n.session_id))),
      h('td', { class: 'nowrap' }, chip(n.kind, 'mono')), h('td', { class: 'nowrap' }, noteState(n)),
      withAbout ? h('td', {}, noteAbout(n)) : null, h('td', { class: 'num' }, deliveredText(n)),
      h('td', { class: 'num nowrap' }, msText(n.time_to_awareness_ms)),
      h('td', {}, noteText(n)))))))
}

// ------------------------------------------------------------------------------- the notes
function notesView () {
  const d = ui.detail
  const params = ui.route.params
  if (!d) return [h('div', { class: 'hero' }, h('div', { class: 'hero-main' }, title('Notes', 'notes')))]
  const sessions = Object.assign(sessionsOf(), d.sessions || {})
  const select = (name, title, options) => {
    const el = h('select', { id: 'filter-' + name, 'data-key': 'filter-' + name, 'aria-label': title },
      h('option', { value: '' }, title + ': all'), options.map(([value, text]) => h('option', { value }, title + ': ' + text)))
    el.value = params.get(name) || ''
    el.addEventListener('change', () => {
      const next = new URLSearchParams(notesQuery(params))
      if (el.value) next.set(name, el.value)
      else next.delete(name)
      const q = next.toString()
      location.hash = '#/notes' + (q ? '?' + q : '')
    })
    return h('span', { class: 'select' }, el, icon('chevron'))
  }
  const m = d.summary
  const waiting = d.notes.filter((n) => n.state === 'pending' || n.state === 'written').length
  const out = [
    h('div', { class: 'hero' }, h('div', { class: 'hero-main' }, title('Notes', 'notes')),
      h('div', { class: 'filters', role: 'search', 'aria-label': 'Filter the notes', 'data-anim': 'filters' },
        select('session', 'Session', Object.keys(d.sessions || {}).map((id) => [id, whoText(sessions, id) + ' · ' + id.slice(0, 8)])),
        select('kind', 'Kind', d.kinds.map((k) => [k, k])), select('state', 'State', d.states.map((s) => [s, s])))),
    h('section', { class: 'kpis', 'aria-label': 'Summary' },
      kpi('total', 'Notes', 'inbox', d.total, 'int', d.total > d.notes.length ? 'the newest ' + d.notes.length + ' listed' : 'newest first'),
      kpi('delivered', 'Delivered', 'check', m.delivered, 'int', 'to their agent'),
      kpi('median', 'Time to awareness', 'gauge', m.median_ms, 'ms',
        m.measured ? 'median of ' + plural(m.measured, 'delivered note', 'delivered notes') : 'no delivered note yet'),
      kpi('waiting', 'Waiting', 'clock', waiting, 'int', 'in their slots'))]
  if (!d.notes.length) {
    out.push(emptyState('No notes', 'No note matches. Notes appear when one session’s change concerns another.', 'none'))
    return out
  }
  out.push(chart(d.notes, m.median_ms))
  out.push(h('section', { class: 'card', 'data-anim': 'table' }, noteTable(d.notes, sessions, true)))
  return out
}

function chart (notes, median) {
  // Time to awareness of the newest notes, oldest on the left. A note with no time yet has a hatched stub.
  const shown = notes.slice(0, 40).reverse()
  const top = Math.max(1, ...shown.map((n) => n.time_to_awareness_ms || 0))
  const stems = shown.map((n) => {
    const ms = n.time_to_awareness_ms
    const stem = h('span', { class: 'stem' + (ms === null ? ' pending' : ''), 'data-anim': 'stem:' + n.id, 'data-fx': 'grow',
      title: n.rule + ' · ' + n.kind + ' · ' + msText(ms) })
    stem.style.setProperty('--h', ms === null ? '6%' : Math.max(3, (ms / top) * 100) + '%')
    return stem
  })
  let line = null
  if (median !== null) {
    // Bars stand in the content box; the line is placed in the padding box, 1.2rem taller.
    line = h('span', { class: 'median' }, h('span', {}, 'median ' + msText(median)))
    line.style.setProperty('--m', 'calc(' + (median / top) + ' * (100% - 1.2rem))')
  }
  return h('section', { class: 'card', 'data-anim': 'chart' },
    h('div', { class: 'card-head' }, label('Time to awareness'), chip(plural(shown.length, 'note', 'notes') + ', newest right', 'quiet')),
    h('div', { class: 'chart', 'aria-hidden': 'true' }, stems, line))
}

// ------------------------------------------------------------------------------- settings
function settingsView () {
  const cfg = ui.detail
  const health = ui.health
  const out = [h('div', { class: 'hero' }, h('div', { class: 'hero-main' }, title('Settings', 'settings')))]
  if (!cfg) return out
  const sw = (name, heading, ic, text) => {
    const on = !!cfg[name]
    const btn = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(on), 'data-key': 'switch-' + name,
      'aria-labelledby': 'title-' + name, 'aria-describedby': 'about-' + name, 'aria-disabled': ui.switchBusy ? 'true' : null })
    btn.addEventListener('click', async () => {
      if (ui.switchBusy) return
      ui.switchBusy = true
      btn.setAttribute('aria-disabled', 'true')
      try {
        const res = await post('/api/config', { [name]: on ? 'off' : 'on' })
        ui.switchResult = { lines: (res.lines || []).concat(res.error ? [res.error] : []), warn: !!res.error }
      } catch (err) {
        if (err instanceof AccessError) return denied()
        ui.switchResult = { lines: [String(err.message || err)], warn: true }
      } finally {
        ui.switchBusy = false
      }
      announce(ui.switchResult.lines.join(' '))
      refresh()
    })
    return h('section', { class: 'card switch-card', 'data-anim': 'sw:' + name }, icon(ic, 'big'),
      h('div', {}, h('h3', { id: 'title-' + name }, heading), h('p', { id: 'about-' + name }, text)), btn)
  }
  const result = ui.switchResult
  out.push(h('div', { class: 'split even' },
    sw('steer', 'Steer', 'steer', 'A note before an edit stops that edit with the reason, so the agent re-plans.'),
    sw('wake', 'Wake', 'bell', 'A strong note wakes a session that waits for its user.')))
  if (result) out.push(h('p', { class: 'result' + (result.warn ? ' warn' : ''), 'data-anim': 'result:' + result.lines.join('|') }, result.lines.join('\n')))
  out.push(h('section', { class: 'card', 'data-anim': 'repos' },
    h('div', { class: 'card-head' }, label('Watched repositories'), chip(String(cfg.repos.length), 'quiet')),
    cfg.repos.length ? h('div', { class: 'rows' }, cfg.repos.map((r) => h('div', { class: 'repo-row' },
      h('h3', {}, icon('repo'), r.name), h('p', { class: 'muted mono' }, r.common_dir), command(r.watch), command(r.unwatch))))
      : h('div', { class: 'rows' }, h('p', { class: 'muted' }, 'None. Watch a repository from inside it:'), command('orch watch'))))
  const c = health && health.coordinator
  const demo = ui.status && ui.status.ui && ui.status.ui.demo
  out.push(h('div', { class: 'split' },
    h('section', { class: 'card', 'data-anim': 'state' }, h('div', { class: 'card-head' }, label('State folder')), h('div', { class: 'rows' },
      command(cfg.state),
      h('dl', { class: 'facts' },
        fact('Coordinator', c ? (c.running ? 'running since ' + clock(c.since) + ', version ' + (c.version || '?') + ', pid ' + (c.pid || '?')
          : 'not running: it starts with the next hook event') : '—'),
        fact('Record', health ? (health.record.exists ? code(health.record.path) : 'not made yet: the coordinator makes it') : '—'),
        fact('Hooks installed in', cfg.settings ? code(cfg.settings)
          : demo ? 'nowhere: the demo writes no Claude Code settings' : 'nowhere yet: run orch init')))),
    h('section', { class: 'card', 'data-anim': 'commands' }, h('div', { class: 'card-head' }, label('Install and remove')),
      h('div', { class: 'rows' }, command(cfg.commands.init), command(cfg.commands.restore)))))
  return out
}

// ------------------------------------------------------------------------------- the sandbox demo
function drawDock () {
  const box = $('demo')
  const d = ui.demo
  if (!d) {
    box.hidden = true
    document.body.classList.remove('has-dock')
    return
  }
  const appearing = box.hidden
  box.hidden = false
  document.body.classList.add('has-dock')
  const done = d.steps.filter((s) => s.done)
  const n = d.steps.length
  const dots = d.steps.map((s) => {
    const dot = h('button', { type: 'button', class: s.done ? 'done' : s.n === d.next ? 'next' : '', 'data-key': 'dot-' + s.n,
      'aria-label': 'Step ' + s.n + (s.done ? ', done' : s.n === d.next ? ', next' : '') + ': ' + s.text, title: s.n + '. ' + s.text })
    dot.addEventListener('click', () => openSheet(s.n, true))
    return dot
  })
  const p = done.length ? (done.length - 1 + (d.next === null ? 0 : 0.5)) / (n - 1) : 0
  const fill = h('span', { class: 'fill' })
  fill.style.setProperty('--p', String(p))
  if (motion.fill !== null && motion.fill !== p && !calm()) {
    fill.animate([{ width: motion.fill * 100 + '%' }, { width: p * 100 + '%' }], { duration: 700, easing: EASE })
  }
  motion.fill = p
  const next = d.next === null ? null : h('button', { type: 'button', class: 'btn light', id: 'demo-next', 'data-key': 'demo-next',
    'aria-disabled': ui.demoBusy ? 'true' : null }, ui.demoBusy ? 'Running…' : 'Next step', icon('next'))
  if (next) next.addEventListener('click', nextStep)
  const out = h('button', { type: 'button', class: 'btn icon', 'data-key': 'demo-out', 'aria-label': 'What the steps printed',
    title: 'What the steps printed' }, icon('terminal'))
  out.addEventListener('click', () => openSheet(done.length ? done[done.length - 1].n : 1, true))
  const now = ui.demoError || (d.next === null ? 'Done. Ctrl+C stops orch ui and removes the sandbox.' : 'Next: ' + d.steps[d.next - 1].text)
  swap(box, [h('div', { class: 'dock-inner' }, h('span', { class: 'label' }, 'Demo'),
    h('div', { class: 'steps-dots', role: 'group', 'aria-label': 'Steps' }, h('span', { class: 'track', 'aria-hidden': 'true' }, fill), dots),
    h('span', { class: 'dock-count' }, done.length + '/' + n, h('span', { class: 'sr-only' }, ' steps done')),
    h('p', { class: 'dock-text', title: now }, now), out, next)])
  if (appearing && !calm()) {
    box.animate([{ opacity: 0, translate: '0 28px' }, { opacity: 1, translate: '0 0' }], { duration: 700, delay: 250, easing: EASE, fill: 'backwards' })
  }
}

function openSheet (n, byUser) {
  ui.sheetStep = n
  ui.sheetOpen = true
  drawSheet(byUser)
}

function closeSheet () {
  const sheet = $('sheet')
  ui.sheetOpen = false
  const hide = () => { if (!ui.sheetOpen) sheet.hidden = true }
  if (!calm()) {
    sheet.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(40px)' }], { duration: 240, easing: 'ease-in' })
      .finished.then(hide, hide)
  } else hide()
  const back = document.querySelector('#demo [data-key="demo-out"]')
  if (back) back.focus({ preventScroll: true })
}

function drawSheet (focus) {
  const sheet = $('sheet')
  const d = ui.demo
  if (!d) sheet.hidden = true
  if (!d || !ui.sheetOpen) return                     // closing: closeSheet hides it after its motion
  const step = d.steps.find((s) => s.n === ui.sheetStep) || d.steps[0]
  const appearing = sheet.hidden
  sheet.hidden = false
  const close = h('button', { type: 'button', class: 'btn icon', 'data-key': 'sheet-close', 'aria-label': 'Close', title: 'Close' }, icon('close'))
  close.addEventListener('click', closeSheet)
  const outputs = step.done
    ? step.outputs.map((o, i) => o.fold
      ? h('details', { class: 'out', 'data-key': 'out-' + step.n + '-' + i },
        h('summary', { 'data-key': 'out-sum-' + step.n + '-' + i }, icon('chevron'), o.label), terminal(null, o.text))
      : terminal(o.label, o.text))
    : [h('p', { class: 'muted' }, 'Not run yet.')]
  swap(sheet, [
    h('div', { class: 'sheet-head' },
      h('div', {}, h('p', { class: 'label' }, 'Step ' + step.n + ' of ' + d.steps.length), h('h2', { id: 'sheet-title', tabindex: '-1' }, step.text)),
      close),
    h('div', { class: 'sheet-body' }, outputs,
      h('div', { class: 'out' }, h('p', { class: 'out-label' }, 'In ', code(d.folder), ', removed when orch ui stops'),
        h('ol', { class: 'step-list' }, d.steps.map((s) => h('li', { class: s.done ? 'done' : s.n === d.next ? 'next' : '' },
          h('span', { class: 'n', 'aria-hidden': 'true' }, s.done ? icon('check') : String(s.n)),
          h('span', {}, h('span', { class: 'sr-only' }, 'Step ' + s.n + (s.done ? ', done: ' : ': ')), s.text))))))])
  if (appearing && !calm()) {
    sheet.animate([{ opacity: 0, transform: 'translateX(48px)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: EASE })
  }
  if (focus) $('sheet-title').focus({ preventScroll: true })
}

async function nextStep () {
  if (ui.demoBusy || !ui.demo || ui.demo.next === null) return
  ui.demoBusy = true
  ui.demoError = null
  drawDock()
  try {
    const d = await post('/api/demo/next')
    cache.set('/api/demo', { etag: null, data: d })
    ui.demo = d
    const last = d.steps.filter((s) => s.done).pop()
    if (last) {
      announce('Step ' + last.n + ' done: ' + last.text)
      openSheet(last.n, false)
    }
  } catch (err) {
    if (err instanceof AccessError) return denied()
    ui.demoError = 'The step did not run: ' + (err.message || err)
  }
  ui.demoBusy = false
  refresh()
}

// ------------------------------------------------------------------------------- start
function themeButton (b) {
  b.replaceChildren(icon({ light: 'sun', dark: 'moon', system: 'monitor' }[b.dataset.themeChoice]))
  b.addEventListener('click', () => {
    const choice = b.dataset.themeChoice
    store(THEME_KEY, choice)
    if (calm() || !document.startViewTransition) return applyTheme(choice)
    // The new theme spreads from the button in a circle (app.css: .theme-turn).
    const box = b.getBoundingClientRect()
    const root = document.documentElement
    const x = box.left + box.width / 2
    const y = box.top + box.height / 2
    root.style.setProperty('--vt-x', x + 'px')
    root.style.setProperty('--vt-y', y + 'px')
    root.style.setProperty('--vt-r', Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y)) + 'px')
    root.classList.add('theme-turn')
    const turn = document.startViewTransition(() => applyTheme(choice))
    turn.finished.finally(() => root.classList.remove('theme-turn'))
  })
}

document.addEventListener('DOMContentLoaded', () => {
  for (const b of document.querySelectorAll('[data-theme-choice]')) themeButton(b)
  applyTheme(stored(THEME_KEY, 'system'))
  document.querySelector('.skip').addEventListener('click', (event) => {
    event.preventDefault()                               // #main is not a route: only move the focus
    $('main').focus()
  })
  window.addEventListener('hashchange', navigate)
  window.addEventListener('resize', moveIndicator)
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && ui.sheetOpen) closeSheet() })
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll() })
  setInterval(tick, 1000)
  markTabs(parseRoute())
  skeleton(parseRoute())
  setTimeout(poll, calm() ? 0 : 300)                   // the loading cards show a moment, then the view rises
})
