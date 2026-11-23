// orch ui: draws what the local server reports about the state folder, and sends the few changes
// the user asks for (a verdict, a claim, a release, a switch) back to it. Everything reaches the
// page through textContent and DOM calls: names and paths come from the user's code.
'use strict'

const POLL_MS = 2000
const THEME_KEY = 'orch-ui-theme'
const ALL_KEY = 'orch-ui-all'
const REPO_KEY = 'orch-ui-repo'
const TOUR_KEY = 'orch-ui-tour-min'
const EASE = 'cubic-bezier(.16, 1, .3, 1)'
const AGENTS = { claude: 'Claude Code', codex: 'Codex', antigravity: 'Antigravity', git: 'plain git', cloud: 'cloud branch', team: 'teammate' }
const STRENGTHS = ['strong', 'medium', 'weak']
const STRENGTH_WORDS = {
  strong: ['Strong', 'Would break when merged. The affected agent hears before its next edit.'],
  medium: ['Medium', 'Both changed the same thing. Both agents are told once.'],
  weak: ['Weak', 'Same file, different parts. Nobody is interrupted.']
}
const RULE_NAMES = {
  O1: 'same file', O2: 'same function', O3: 'breaking change, used in the other session', O4: 'removed, still used',
  O5: 'same HTTP route', O6: 'callers left behind', O7: 'declared or claimed work overlaps', W1: 'same folder'
}
const VERDICTS = [['real', 'Real'], ['harmless', 'Harmless'], ['false', 'False alarm']]
const PALETTE = ['#A9BCD3', '#E3B7AF', '#FBEFEC']
const PAGES = [
  ['home', 'Home', 'home', 'h'], ['projects', 'Projects', 'repo', 'p'], ['sessions', 'Sessions', 'users', 's'], ['overlaps', 'Overlaps', 'overlap', 'o'],
  ['activity', 'Activity', 'activity', 'a'], ['claims', 'Claims', 'lock', 'c'], ['merge', 'Merge order', 'merge', 'm']
]
const ICONS = {
  home: 'M3 11.5 12 4l9 7.5M5.5 10v9.5h13V10',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM3 20c.5-3.5 3-5.5 6-5.5s5.5 2 6 5.5M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.8c1.8.7 2.8 2.5 3 5.2',
  overlap: 'M9 15a6 6 0 1 0 0-12 6 6 0 0 0 0 12ZM15 21a6 6 0 1 0 0-12 6 6 0 0 0 0 12Z',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  lock: 'M6 11h12v9H6ZM8.5 11V8a3.5 3.5 0 0 1 7 0v3',
  merge: 'M6 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM6 22a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM6 6v12M6 8c0 3 4 4 10 4',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19.4 13.5l1.6 1.2-2 3.4-1.9-.7a7 7 0 0 1-2.1 1.2L14.5 21h-5l-.5-2.4a7 7 0 0 1-2.1-1.2l-1.9.7-2-3.4 1.6-1.2a7 7 0 0 1 0-3L3 9.3l2-3.4 1.9.7A7 7 0 0 1 9 5.4L9.5 3h5l.5 2.4a7 7 0 0 1 2.1 1.2l1.9-.7 2 3.4-1.6 1.2a7 7 0 0 1 0 3Z',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM20 20l-4-4',
  refresh: 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM9.5 9.5a2.5 2.5 0 0 1 4.8 1c0 1.7-2.3 2-2.3 3.5M12 17h.01',
  sun: 'M12 3v1.6M12 19.4V21M4.6 4.6l1.1 1.1M18.3 18.3l1.1 1.1M3 12h1.6M19.4 12H21M4.6 19.4l1.1-1.1M18.3 5.7l1.1-1.1M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z',
  moon: 'M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6Z',
  copy: 'M9 9h10.5v10.5H9ZM5 15V5h10',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  back: 'M15 6l-6 6 6 6',
  next: 'M9 6l6 6-6 6',
  close: 'M6 6l12 12M18 6 6 18',
  note: 'M4 5h16v11H9l-5 4Z',
  change: 'M4 20h4l10-10-4-4L4 16ZM13.5 6.5l4 4',
  push: 'M12 19V5M6 11l6-6 6 6',
  play: 'M8 5v14l11-7Z',
  steer: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z',
  repo: 'M5 4.5h11.5a2 2 0 0 1 2 2V20H7a2 2 0 0 1-2-2ZM5 18a2 2 0 0 1 2-2h11.5',
  minus: 'M6 12h12',
  plus: 'M12 6v12M6 12h12',
  done: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM8 12.5l3 3 5-6'
}

// ------------------------------------------------------------------------------- the theme
// Applied before the body is drawn: the script loads in <head>.
function stored (key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}
function store (key, value) {
  try { localStorage.setItem(key, value) } catch { /* a private window: the choice lasts this visit */ }
}
function applyTheme (choice) {
  if (choice === 'light' || choice === 'dark') document.documentElement.dataset.theme = choice
  else delete document.documentElement.dataset.theme
}
function themeChoice () {
  return stored(THEME_KEY, 'system')
}
function darkNow () {
  const choice = themeChoice()
  if (choice === 'dark' || choice === 'light') return choice === 'dark'
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}
function setTheme (choice) {
  store(THEME_KEY, choice)
  applyTheme(choice)
  drawThemeButton()
}
applyTheme(themeChoice())

// ------------------------------------------------------------------------------- DOM helpers
function h (tag, attrs, ...kids) {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue
    if (k === 'class') el.className = v
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v)
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
  for (const kid of kids) if (kid) el.append(kid)
  return el
}
function icon (name, cls) {
  return svg('svg', { class: 'ic' + (cls ? ' ' + cls : ''), viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false' },
    svg('path', { d: ICONS[name] || ICONS.help }))
}
const $ = (id) => document.getElementById(id)
const code = (text) => h('code', {}, text)
const arrowHead = (cls) => svg('svg', { class: cls, viewBox: '0 0 14 14', 'aria-hidden': 'true', focusable: 'false' }, svg('path', { d: 'M3 2.5 L10 7 L3 11.5' }))
const chip = (text, cls, dot) => h('span', { class: 'chip' + (cls ? ' ' + cls : '') }, dot ? h('span', { class: 'dot' }) : null, text)
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many)

function baseName (path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop()
}
function relPath (top, path) {
  if (!top || !path) return path || ''
  const t = top.replace(/\\/g, '/').replace(/\/+$/, '') + '/'
  const p = path.replace(/\\/g, '/')
  return p.toLowerCase().startsWith(t.toLowerCase()) ? p.slice(t.length) : path
}
function middle (text, keep) {
  // A long path, cut in the middle; the whole one is in its title.
  text = String(text || '')
  return text.length <= keep ? text : text.slice(0, Math.ceil(keep / 2) - 1) + '…' + text.slice(-Math.floor(keep / 2))
}
const symName = (s) => String(s || '').split('::').pop()
const symFile = (s) => String(s || '').split('::')[0]
function safeDecode (text) {
  try { return decodeURIComponent(text) } catch { return text }
}

// ------------------------------------------------------------------------------- times
function ago (ms) {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return s + ' s ago'
  const m = Math.round(s / 60)
  if (m < 60) return m + ' min ago'
  const hr = Math.round(m / 60)
  if (hr < 48) return hr + ' h ago'
  return Math.round(hr / 24) + ' days ago'
}
function clock (iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d) ? '—' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
function stamp (iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d) ? '—' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function since (iso) {
  return iso ? h('time', { datetime: iso, 'data-since': iso, title: new Date(iso).toLocaleString() }, ago(Date.now() - Date.parse(iso))) : '—'
}
function msText (ms) {
  if (ms === null || ms === undefined) return '—'
  return ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + ' s'
}
function today (iso) {
  return iso && new Date(iso).toDateString() === new Date().toDateString()
}
function tick () {
  const now = Date.now()
  for (const el of document.querySelectorAll('time[data-since]')) el.textContent = ago(now - Date.parse(el.dataset.since))
}

// ------------------------------------------------------------------------------- motion
// What is new in a view arrives once (it rises); a redraw after a poll leaves what is already
// there alone. Skipped when the system asks for less motion.
const seen = new Set()
function calm () {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
function settle (box) {
  const still = calm() || !box.animate
  let i = 0
  for (const el of box.querySelectorAll('[data-anim]')) {
    if (seen.has(el.dataset.anim)) continue
    seen.add(el.dataset.anim)
    if (!still) {
      el.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
        { duration: 320, delay: Math.min(i++ * 35, 420), easing: EASE, fill: 'backwards' })
    }
  }
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
  route: null, status: null, findings: null, notes: null, push: null, detail: null, config: null, health: null, demo: null,
  showAll: stored(ALL_KEY, '0') === '1', repo: stored(REPO_KEY, ''), lastOk: null, lost: false, denied: false, dirty: true,
  busy: false, rescanning: false, labels: {}, doctor: null, doctorBusy: false, claimOpen: false, claimResult: null, demoBusy: false,
  demoError: null, tourMin: stored(TOUR_KEY, '0') === '1', ofilter: 'all', showResolved: false, afilter: 'all'
}

function parseRoute () {
  const raw = location.hash.replace(/^#/, '') || '/'
  const cut = raw.indexOf('?')
  const path = cut < 0 ? raw : raw.slice(0, cut)
  const params = new URLSearchParams(cut < 0 ? '' : raw.slice(cut + 1))
  const parts = path.split('/').filter(Boolean)
  const rest = safeDecode(parts.slice(1).join('/'))
  const name = parts[0] || 'home'
  if (name === 'session' && rest) return { name: 'session', id: rest, params }
  if (name === 'overlap' && rest) return { name: 'overlap', key: rest, params }
  if (name === 'notes') return { name: 'activity', params }
  if (['projects', 'sessions', 'overlaps', 'activity', 'claims', 'merge', 'settings'].includes(name)) return { name, params }
  return { name: 'home', params }
}

function urlsFor (route, demo) {
  const want = {}
  if (demo) want.demo = '/api/demo'
  const n = route.name
  if (n === 'home' || n === 'overlaps' || n === 'activity') want.findings = '/api/findings?state=all'
  if (n === 'home' || n === 'activity') { want.notes = '/api/notes?'; want.push = '/api/push' }
  if (n === 'session') want.detail = '/api/sessions/' + encodeURIComponent(route.id)
  if (n === 'overlap') want.detail = '/api/findings/' + encodeURIComponent(route.key)
  if (n === 'settings') { want.config = '/api/config'; want.health = '/api/health' }
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
    for (const k of ['findings', 'notes', 'push', 'detail', 'config', 'health', 'demo']) ui[k] = null
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
  drawTop()
  timer = setTimeout(poll, document.hidden ? POLL_MS * 5 : POLL_MS)
}

function refresh () {
  ui.dirty = true
  poll()
}

async function navigate () {
  const route = parseRoute()
  const main = $('main')
  closeLayers()
  if (route.name !== 'claims') { ui.claimOpen = false; ui.claimOpenSeen = false; ui.claimResult = null }
  clearTimeout(timer)
  const gen = ++generation                             // a poll on its way belongs to the old view
  ui.dirty = true
  seen.clear()
  drawSide(route)
  main.focus({ preventScroll: true })
  if (!calm() && main.firstChild && main.animate) {
    await main.animate([{ opacity: 1 }, { opacity: 0, transform: 'translateY(-4px)' }],
      { duration: 140, easing: 'ease-in', fill: 'forwards' }).finished.catch(() => {})
  }
  if (gen !== generation) return
  window.scrollTo(0, 0)
  clearTimeout(skeletonTimer)
  skeletonTimer = setTimeout(() => skeleton(), 220)    // only when the answer is slow
  poll()
}

function go (hash) {
  if (location.hash === hash || (hash === '#/' && !location.hash)) refresh()
  else location.hash = hash
}

// ------------------------------------------------------------------------------- drawing
function swap (box, nodes) {
  // Keep the keyboard where it was across redraws: focus and open <details> are restored by key.
  const active = document.activeElement
  const keyOf = (el) => el.dataset.key || el.getAttribute('href') || el.id || null
  const focus = active && box.contains(active) && active !== box ? keyOf(active) : null
  const open = [...box.querySelectorAll('details[open][data-key]')].map((d) => d.dataset.key)
  const values = [...box.querySelectorAll('input[data-key], select[data-key]')].map((el) => [el.dataset.key, el.type === 'checkbox' ? el.checked : el.value])
  box.replaceChildren(...nodes.filter((n) => n !== null && n !== undefined))
  for (const d of box.querySelectorAll('details[data-key]')) if (open.includes(d.dataset.key)) d.open = true
  for (const [key, value] of values) {
    const el = box.querySelector('[data-key="' + CSS.escape(key) + '"]')
    if (el && el.dataset.keep !== undefined) { if (el.type === 'checkbox') el.checked = value; else el.value = value }
  }
  if (focus) {
    const again = [...box.querySelectorAll('[data-key], [href], [id]')].find((el) => keyOf(el) === focus)
    if (again) again.focus({ preventScroll: true })
  }
}

function render () {
  clearTimeout(skeletonTimer)
  const main = $('main')
  for (const a of main.getAnimations()) a.cancel()
  const views = { home: homeView, projects: projectsView, sessions: sessionsView, session: sessionView, overlaps: overlapsView, overlap: overlapView,
    activity: activityView, claims: claimsView, merge: mergeView, settings: settingsView }
  swap(main, [h('div', { class: 'view' }, versionNotices(), (views[ui.route.name] || homeView)())])
  settle(main)
  drawSide(ui.route)
  drawTour()
  tick()
}

function skeleton () {
  const main = $('main')
  for (const a of main.getAnimations()) a.cancel()
  main.replaceChildren(h('div', { class: 'view' }, h('div', { class: 'skel line' }), h('div', { class: 'skel' }), h('div', { class: 'skel tall' })))
}

function denied () {
  ui.denied = true
  clearTimeout(timer)
  clearTimeout(skeletonTimer)
  $('tour').hidden = true
  closeLayers()
  swap($('main'), [h('div', { class: 'view' }, h('div', { class: 'notice bad', role: 'alert' },
    h('strong', {}, 'This page can no longer reach orch ui. '),
    'Open the link that orch ui printed in your terminal: it changes each time orch ui starts.'))])
  drawTop()
}

function announce (text) {
  const el = $('announce')
  el.textContent = ''
  requestAnimationFrame(() => { el.textContent = text })
}

// ------------------------------------------------------------------------------- what the status says
function allRepos () {
  return (ui.status && ui.status.repositories) || []
}
function repos () {
  const all = allRepos()
  const picked = all.filter((r) => r.common_dir === ui.repo)
  return picked.length ? picked : all
}
function inChosen (commonDir) {
  return repos().some((r) => r.common_dir === commonDir)
}
function sessionsById () {
  const out = {}
  for (const repo of allRepos()) for (const s of repo.sessions) out[s.id] = s
  return out
}
function liveSessions () {
  return repos().flatMap((r) => r.sessions.filter((s) => s.state === 'live'))
}
function demoOn () {
  return !!(ui.status && ui.status.ui && ui.status.ui.demo)
}
function openFindings () {
  const list = (ui.findings && ui.findings.findings) || []
  return list.filter((f) => f.state === 'open' && inChosen(f.repository.common_dir) && (ui.showAll || !f.latent))
}
function needsYou () {
  return openFindings().filter((f) => f.strength === 'strong')
}
function strongCount () {
  return repos().reduce((n, r) => n + r.overlaps.filter((o) => o.strength === 'strong').length, 0)
}

// ------------------------------------------------------------------------------- names
let known = {}                     // every session the current view knows, by id
function learn (map) {
  Object.assign(known, map || {})
}
function sname (s, id) {
  if (!s) return String(id || '').slice(0, 8) || '?'
  if (s.agent === 'team') {          // a teammate's branch, by who works on it: "bob · feat/refunds"
    const who = (s.people || []).length ? s.people.join(' and ') : (s.own ? 'You, on another machine' : 'A teammate')
    return who + ' · ' + String(s.branch || '').replace(/^[^/]*\//, '')
  }
  return baseName(s.worktree) || (s.branch ? s.branch : String(s.id).slice(0, 8))
}
const N = (id) => sname(known[id], id)
function colorOf (id) {
  let n = 0
  for (const c of String(id || '')) n = (n * 31 + c.charCodeAt(0)) >>> 0
  return PALETTE[n % PALETTE.length]
}
function avatar (s, id, size) {
  const sid = (s && s.id) || id
  const git = s && (s.agent === 'git' || s.agent === 'cloud' || s.agent === 'team')
  const parts = sname(s, sid).split(/[-_./ ]+/).filter(Boolean)
  const el = h('span', { class: 'av' + (size ? ' ' + size : '') + (git ? ' git' : ''), 'aria-hidden': 'true' },
    (parts[parts.length - 1] || '?').slice(0, 1).toUpperCase())
  if (!git) el.style.setProperty('--c', colorOf(sid))
  return el
}
function agentName (agent) {
  return AGENTS[agent] || agent || 'agent'
}
function stateOf (s) {
  if (s.state !== 'live') return ['Ended', '', s.note || 'ended']
  if (s.agent === 'git') return ['Plain git', '', 'seen through git']
  if (s.agent === 'cloud') return ['Cloud branch', '', 'fetched from the remote']
  if (s.agent === 'team') return ['Teammate', '', 'read from their commits']
  if (s.turn === 'working') return ['Working', 'ok', 'working on a turn']
  if (s.turn === 'idle') return ['Waiting', 'medium', 'waiting for its user']
  return ['Idle', '', 'idle']
}
function stateChip (s) {
  const [word, cls] = stateOf(s)
  return chip(word, cls, true)
}
function doing (s) {
  const files = Object.keys((s.changes && s.changes.files) || {})
  if (!files.length) return s.state === 'live' ? 'No changes yet' : (s.note || 'Ended')
  const first = baseName(files[0])
  return 'Changed ' + first + (files.length > 1 ? ' and ' + plural(files.length - 1, 'other file', 'other files') : '')
}

// ------------------------------------------------------------------------------- overlaps in words
function fnName (o) {
  const n = symName(o.symbol)
  return n ? n + '()' : baseName(o.file)
}
function pair (o) {
  return o.sessions || []
}
function overlapTitle (o) {
  const d = o.detail || {}
  const [a, b] = pair(o)
  if (o.rule === 'O3') return N(o.changer) + ' changed ' + fnName(o) + '; ' + N(o.receiver) + ' uses it'
  if (o.rule === 'O4') return N(o.changer) + ' removed ' + fnName(o) + '; ' + N(o.receiver) + ' still uses it'
  if (o.rule === 'O6') {
    const n = (d.left || []).length
    return plural(n, 'caller still uses', 'callers still use') + ' the old ' + fnName(o)
  }
  if (o.rule === 'O2') return N(a) + ' and ' + N(b) + ' both changed ' + fnName(o)
  if (o.rule === 'O5') return N(a) + ' and ' + N(b) + ' both changed the route ' + (d.method || '') + ' ' + (d.path || '')
  if (o.rule === 'O1') return N(a) + ' and ' + N(b) + ' both edited ' + baseName(o.file)
  if (o.rule === 'W1') return N(a) + ' and ' + N(b) + ' work in the same folder'
  if (o.rule === 'O7') return N(a) + ' and ' + N(b) + ' both reserved ' + (d.what || fnName(o))
  return RULE_NAMES[o.rule] || o.rule
}
function linesOf (uses) {
  const lines = (uses || []).flatMap((u) => u.lines || [])
  return lines.length ? (lines.length === 1 ? 'line ' : 'lines ') + lines.slice(0, 4).join(', ') : ''
}
function overlapWhere (o) {
  const d = o.detail || {}
  if (o.rule === 'O3' || o.rule === 'O4') {
    const where = linesOf(d.uses)
    return 'In ' + N(o.receiver) + '’s copy: ' + o.file + (where ? ', ' + where : '') + (d.latent ? '. It is not editing that file yet.' : '')
  }
  if (o.rule === 'O6') {
    const left = (d.left || []).map((c) => symName(c.symbol) + '() in ' + (c.file || symFile(c.symbol)))
    const many = left.length !== 1
    return left.slice(0, 3).join(', ') + (left.length > 3 ? ' and more' : '') + '. No session is updating ' +
      (many ? 'them, so they show' : 'it, so it shows') + ' at git push.'
  }
  if (o.rule === 'O2') return 'In ' + symFile(o.symbol)
  if (o.rule === 'O5') return 'Declared in ' + o.file
  if (o.rule === 'O1') return o.file + ' · different changes'
  if (o.rule === 'W1') return 'In ' + baseName(d.worktree || '') + ': give each session its own worktree'
  if (o.rule === 'O7') return Object.entries(d.how || {}).map(([sid, how]) => how + ' by ' + N(sid)).join(', ')
  return o.file || ''
}
function consequence (o) {
  if (o.rule === 'O3' || o.rule === 'O4') return 'If both branches merge as they are, ' + N(o.receiver) + '’s call to ' + fnName(o) + ' breaks.'
  if (o.rule === 'O6') return 'The push still goes through, with one line about them.'
  if (o.rule === 'O5') return 'Both should agree on the route’s method and path before either merges.'
  if (o.rule === 'O2') return 'Both changed it; agree before either merges.'
  if (o.rule === 'O1') return 'Different parts of one file: nobody is interrupted. It shows here and in orch status.'
  if (o.rule === 'W1') return 'Their changes to the shared files cannot be told apart. Each agent was told once.'
  return ''
}
function toldOf (f) {
  // How the sessions heard of a finding, from its notes.
  const notes = f.notes || []
  if (!notes.length) return f.rule === 'O1' ? ['Silent', ''] : ['No note', '']
  const got = notes.filter((n) => n.state === 'delivered')
  if (got.length) {
    const first = got.find((n) => n.delivered) || got[0]
    const how = { edit: 'Told before its edit', next: 'Told with its next step', stop: 'Told at turn end', wake: 'Woken with a note' }
    return [how[first.kind] || 'Told', 'brand']
  }
  if (notes.some((n) => n.state === 'written' || n.state === 'pending')) return ['Note waiting', 'medium']
  return ['Note taken back', '']
}
function overlapFlow (o) {
  const box = (id, role) => {
    const s = known[id]
    return h('a', { class: 'who', href: '#/session/' + encodeURIComponent(id) }, avatar(s, id),
      h('div', {}, h('div', { class: 'role' }, role), h('b', {}, N(id)), h('div', { class: 'meta' }, s ? agentName(s.agent) + (s.branch ? ' · ' + s.branch : '') : '')))
  }
  const d = o.detail || {}
  if (o.rule === 'O3' || o.rule === 'O4') {
    const what = o.rule === 'O4' ? 'removed ' + fnName(o) : (d.reasons && d.reasons[0]) || 'changed ' + fnName(o)
    return h('div', { class: 'flow' }, box(o.changer, 'Changed it'), h('div', { class: 'arrow' }, h('span', {}, what), h('i')),
      box(o.receiver, 'Relies on it'))
  }
  if (o.rule === 'O6') {
    const n = (d.left || []).length
    return h('div', { class: 'flow' }, box(o.changer, 'Changed it'), h('div', { class: 'arrow' }, h('span', {}, (d.reasons && d.reasons[0]) || 'changed ' + fnName(o)), h('i')),
      h('div', { class: 'who' }, h('span', { class: 'av git', 'aria-hidden': 'true' }, String(n)),
        h('div', {}, h('div', { class: 'role' }, 'Left behind'), h('b', {}, plural(n, 'caller', 'callers')), h('div', { class: 'meta' }, 'no session updated'))))
  }
  const [a, b] = pair(o)
  return h('div', { class: 'flow' }, box(a, 'Session'), h('div', { class: 'arrow both' + (o.strength === 'weak' ? ' weak' : '') },
    h('span', {}, o.rule === 'O1' ? 'both edited ' + baseName(o.file) : 'both changed ' + fnName(o)), h('i')), box(b, 'Session'))
}
function noteWhen (n) {
  if (n.kind === 'edit') return 'before editing ' + (n.file || 'the file')
  if (n.kind === 'next') return 'with its next step'
  if (n.kind === 'stop') return 'when it tried to finish its turn'
  if (n.kind === 'wake') return 'to wake it'
  return n.kind
}
function noteHeadline (n) {
  const about = symName(n.symbol) ? symName(n.symbol) + '()' : baseName(n.file)
  if (n.state === 'delivered') return N(n.session_id) + ' was told about ' + about + ' ' + noteWhen(n)
  if (n.state === 'withdrawn') return 'A note to ' + N(n.session_id) + ' about ' + about + ' was taken back'
  return 'A note waits for ' + N(n.session_id) + ' about ' + about + ', ' + noteWhen(n)
}

// ------------------------------------------------------------------------------- shared pieces
function copyText (text, copied) {
  const done = copied || (() => toast('Copied: ' + text))
  try {
    navigator.clipboard.writeText(text).then(done, () => toast('Press Ctrl+C to copy: ' + text))
  } catch {
    toast('Copy it by hand: ' + text)
  }
}
const justCopied = new Map()       // command -> the timer that puts its button back to Copy
function copyButtons (text) {
  return [...document.querySelectorAll('.cmd-copy')].filter((b) => b.dataset.key === 'copy:' + text)
}
function markCopied (text) {
  clearTimeout(justCopied.get(text))
  for (const b of copyButtons(text)) b.textContent = 'Copied'
  announce('Copied')
  justCopied.set(text, setTimeout(() => {
    justCopied.delete(text)
    for (const b of copyButtons(text)) b.textContent = 'Copy'
  }, 1500))
}
function command (text) {
  return h('div', { class: 'cmd' }, arrowHead('cmd-prompt'), h('code', { title: text }, text),
    h('button', { type: 'button', class: 'cmd-copy', 'aria-label': 'Copy ' + text, 'data-key': 'copy:' + text,
      onclick: () => copyText(text, () => markCopied(text)) }, justCopied.has(text) ? 'Copied' : 'Copy'))
}
// One primary button a screen: while the demo tour offers its next step, that is the one.
function tourLeads () {
  return demoOn() && !!ui.demo && ui.demo.next !== null
}
function empty (heading, body, extra) {
  return h('div', { class: 'card empty' }, h('h3', {}, heading), h('p', {}, body), extra || null)
}
function pageHead (title, lead, extra) {
  return h('div', { class: 'row-flex', 'data-anim': 'head:' + title },
    h('div', { class: 'grow', style: null }, h('h1', { class: 'h1' }, title), lead ? h('p', { class: 'lead' }, lead) : null), extra || null)
}
function backTo (hash, text) {
  return h('a', { class: 'btn sm quiet back', href: hash }, icon('back', 'sm'), text)
}
function sectionHead (title, count, more) {
  return h('div', { class: 'section-head' }, h('h2', { class: 'h2' }, title), count !== undefined && count !== null ? chip(String(count), count ? '' : '') : null,
    h('span', { class: 'grow' }), more || null)
}
function strengthChip (s) {
  return chip((STRENGTH_WORDS[s] || [s])[0], s, true)
}
function noRepos () {
  const all = ui.status && ui.status.tracking && ui.status.tracking.all
  return [...unwatchedCards(), all
    ? empty('No projects yet', 'Orchestraty follows every project your agents work in. A project shows here after the first event from an agent working in it.')
    : empty('No projects yet', 'Run orch init and let it follow every project your agents work in, or run orch watch in a project.', command('orch init')),
  connectionPanel()]
}
// Each agent's hooks: whether they are set up, and whether an event from that agent has reached
// a project yet. `only` keeps the rows in those states (null when none is).
const CONNECTION = { not_configured: ['Not set up', ''], waiting: ['Waiting for first event', 'medium'],
  event_received: ['Connected', 'ok'], event_issue: ['Event needs attention', 'strong'], config_error: ['Configuration needs attention', 'strong'] }
function connectionPanel (only) {
  const rows = ((ui.status && !demoOn() && ui.status.connections) || []).filter((row) => !only || only.includes(row.status))
  if (!rows.length) return null
  const c = ui.status.coordinator
  return h('section', { class: 'section', 'aria-labelledby': 'agents-head' },
    h('div', { class: 'section-head' }, h('h2', { class: 'h2', id: 'agents-head' }, 'Agent connections')),
    h('div', { class: 'card' }, rows.map((row) => {
      const [label, cls] = CONNECTION[row.status] || [row.status, '']
      return h('div', { class: 'setting', 'data-anim': 'agent:' + row.agent },
        h('div', { class: 'min0' }, h('b', {}, row.name), h('p', { class: 'meta' }, row.message),
          row.last_event ? h('p', { class: 'meta' }, 'Last event ', since(row.last_event)) : null,
          (row.paths || []).map((path) => h('p', { class: 'meta mono', title: path }, middle(path, 70))),
          row.status === 'not_configured' || row.status === 'config_error' ? command(row.command) : null),
        chip(label, cls, true))
    })),
    c && !c.running ? h('p', { class: 'meta' }, 'The background process isn’t running: the next agent event starts it. If an agent’s turn brings no event, run orch doctor.') : null)
}
// After an upgrade: this dashboard, or the background process, still runs the earlier version.
function versionNotices () {
  const u = ui.status && ui.status.ui
  const c = ui.status && ui.status.coordinator
  if (!u || u.demo) return []
  const now = u.installed || u.version
  if (u.installed && u.installed !== u.version) {
    return [h('div', { class: 'notice', role: 'status', 'data-anim': 'upgraded-ui' },
      h('b', {}, 'Orchestraty ' + u.installed + ' is installed. '),
      'This dashboard still runs ' + u.version + ': press Ctrl+C where orch ui runs, then run orch ui again.')]
  }
  if (c && c.running && c.version && c.version !== now) {
    return [h('div', { class: 'notice', role: 'status', 'data-anim': 'upgraded-coordinator' },
      h('b', {}, 'The background process still runs ' + c.version + '. '),
      'It restarts by itself with ' + now + ' within a minute, or now with orch coordinator stop.')]
  }
  return []
}
// Repositories agents work in that Orchestraty does not watch, so their sessions are not compared.
function unwatchedCards () {
  const list = (ui.status && ui.status.unwatched) || []
  return list.map((r) => h('article', { class: 'card need medium', 'data-anim': 'unwatched:' + r.common_dir },
    h('span', { class: 'bar' }),
    h('div', { class: 'min0' },
      h('h3', {}, plural(r.sessions, 'session works', 'sessions work') + ' in ' + r.name + ', which isn’t watched'),
      h('p', { class: 'meta' }, 'Orchestraty doesn’t compare them with your other sessions until you watch it.')),
    h('div', { class: 'actions' },
      h('button', { type: 'button', class: 'btn sm primary', 'data-key': 'watch:' + r.common_dir, onclick: () => watchRepo(r) },
        'Watch ' + r.name))))
}

// ------------------------------------------------------------------------------- home
function greeting () {
  const hr = new Date().getHours()
  return hr < 5 ? 'Good evening' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening'
}
function homeView () {
  learn(sessionsById())
  learn(ui.findings && ui.findings.sessions)
  if (!allRepos().length) return [pageHead(greeting()), ...noRepos()]
  const live = liveSessions()
  const needs = needsYou()
  const names = repos().map((r) => r.name).join(', ')
  const lead = (live.length ? plural(live.length, 'agent is', 'agents are') + ' working on ' + names + '. '
    : 'No agent is working on ' + names + ' right now. ') +
    (needs.length === 0 ? 'Nothing needs you.' : needs.length === 1 ? 'One thing needs a look.' : needs.length + ' things need a look.')
  const left = [...unwatchedCards(), connectionPanel(['event_issue', 'config_error']), h('section', { class: 'section', 'aria-labelledby': 'needs' },
    h('div', { class: 'section-head' }, h('h2', { class: 'h2', id: 'needs' }, 'Needs you'), needs.length ? chip(String(needs.length), 'strong') : null),
    needs.length ? needs.map(needCard)
      : h('div', { class: 'card calm', 'data-anim': 'calm' }, icon('done'),
        h('div', {}, h('b', {}, 'All clear'), h('p', { class: 'meta' }, openFindings().length
          ? plural(openFindings().length, 'smaller overlap is', 'smaller overlaps are') + ' being handled without you.'
          : 'Your agents’ work doesn’t touch.'))))]
  left.push(h('section', { class: 'section' },
    sectionHead('Sessions now', null, h('a', { class: 'btn sm quiet', href: '#/sessions' }, 'All sessions')),
    live.length ? h('div', { class: 'card rows' }, live.map(lane))
      : empty('No agent is working right now', 'A session shows here as soon as an agent with the hooks starts in a watched repository, or one of its worktrees changes.')))
  const feed = feedItems().slice(0, 5)
  left.push(h('section', { class: 'section' },
    sectionHead('Recent activity', null, h('a', { class: 'btn sm quiet', href: '#/activity' }, 'All activity')),
    feed.length ? h('div', { class: 'card feed' }, feed.map((it) => feedRow(it, false))) : empty('Nothing yet', 'Notes, push lines and sessions show here as they happen.')))
  return [pageHead(greeting(), lead), h('div', { class: 'grid-home' }, h('div', { class: 'stack', style: null }, left), glance(live))]
}
function needCard (f, i) {
  const [told, cls] = toldOf(f)
  const label = ui.labels[f.key] || (f.label && f.label.verdict)
  const secondary = f.rule === 'O6'
    ? h('button', { type: 'button', class: 'btn sm', onclick: () => copyText('orch status') }, 'Copy orch status')
    : h('a', { class: 'btn sm', href: '#/session/' + encodeURIComponent(f.changer || pair(f)[0]) }, 'See ' + N(f.changer || pair(f)[0]))
  return h('article', { class: 'card need ' + f.strength, 'data-anim': 'need:' + f.key },
    h('span', { class: 'bar' }),
    h('div', { class: 'min0' },
      h('div', { class: 'row-flex' }, strengthChip(f.strength), chip(told, cls), label ? chip('You said: ' + label, 'ok') : null,
        h('span', { class: 'faint' }, since(f.first_seen))),
      h('h3', {}, overlapTitle(f)), h('p', { class: 'meta' }, overlapWhere(f))),
    h('div', { class: 'actions' }, secondary,
      h('a', { class: 'btn sm' + (i === 0 && !tourLeads() ? ' primary' : ''), href: '#/overlap/' + encodeURIComponent(f.key), 'data-key': 'review:' + f.key }, 'Review', arrowHead('btn-arrow'))))
}
function strongestFor (id) {
  let best = null
  for (const r of repos()) {
    for (const o of r.overlaps) {
      if ((o.sessions || []).includes(id) || o.changer === id || o.receiver === id) {
        if (!best || STRENGTHS.indexOf(o.strength) < STRENGTHS.indexOf(best)) best = o.strength
      }
    }
  }
  return best
}
function lane (s) {
  const top = strongestFor(s.id)
  return h('a', { class: 'lane', href: '#/session/' + encodeURIComponent(s.id), 'data-anim': 'lane:' + s.id },
    avatar(s),
    h('div', { class: 'min0' }, h('div', { class: 'row-flex' }, h('b', {}, sname(s)), h('span', { class: 'meta' }, agentName(s.agent))),
      h('div', { class: 'meta doing' }, doing(s))),
    h('div', { class: 'tags' }, top ? chip((STRENGTH_WORDS[top] || [top])[0] + ' overlap', top) : null, stateChip(s)))
}
function glance (live) {
  const notes = (ui.notes && ui.notes.notes) || []
  const sent = notes.filter((n) => n.state === 'delivered' && today(n.delivered || n.created)).length
  const m = ui.notes && ui.notes.summary
  const agents = {}
  for (const s of live) agents[agentName(s.agent)] = (agents[agentName(s.agent)] || 0) + 1
  const c = ui.status && ui.status.coordinator
  return h('aside', { class: 'stack', 'aria-label': 'At a glance' },
    h('h2', { class: 'h2' }, 'At a glance'),
    h('div', { class: 'card stat', 'data-anim': 'g:live' }, h('span', { class: 'meta' }, 'Live sessions'), h('b', {}, String(live.length)),
      h('span', { class: 'faint' }, Object.entries(agents).map(([k, v]) => v + ' ' + k).join(' · ') || 'none right now')),
    h('div', { class: 'card stat', 'data-anim': 'g:notes' }, h('span', { class: 'meta' }, 'Notes delivered today'), h('b', {}, String(sent)),
      h('span', { class: 'faint' }, 'each before the agent built on the old form')),
    h('div', { class: 'card stat', 'data-anim': 'g:tta' }, h('span', { class: 'meta' }, 'From change to note'), h('b', {}, msText(m && m.median_ms)),
      h('span', { class: 'faint' }, m && m.measured ? 'median of ' + plural(m.measured, 'note', 'notes') : 'no note delivered yet')),
    h('div', { class: 'card pad stack', 'data-anim': 'g:coord' }, h('span', { class: 'meta' }, 'Coordinator'),
      h('div', { class: 'row-flex' }, chip(c && c.running ? 'Running' : 'Stopped', c && c.running ? 'ok' : 'medium', true),
        h('span', { class: 'meta' }, c && c.running ? 'since ' + clock(c.since) : 'starts with the next agent event')),
      h('button', { type: 'button', class: 'btn sm', onclick: () => { go('#/settings'); runDoctor() } }, 'Run health checks')),
    teamCards())
}

// Team sync, one card a repository that has it. A teammate's words (handle, branch, ticket, task
// line) come from their record: like every name here, they reach the page as text, never as HTML.
function teamCards () {
  return repos().filter((r) => r.team).map((r) => {
    const t = r.team
    const pol = t.policy || {}
    const sharing = t.on && pol.sync
    const who = t.me ? 'as ' + t.me.person + (t.me.label ? ' on ' + t.me.label : '') : 'no git email is set'
    const held = (t.withheld_24h || []).reduce((n, w) => n + w.count, 0)
    const rows = []
    for (const w of t.writers || []) {
      if (w.verdict !== 'admitted') {
        rows.push(h('li', { class: 'meta' }, 'Dropped: ' + w.person + ' (' + w.why + ')'))
        continue
      }
      for (const e of w.entries || []) {
        if (e.pushed && (t.sessions || []).some((s) => s.branch === t.remote + '/' + e.branch)) continue
        rows.push(h('li', {}, h('b', {}, w.person), ' ', h('span', { class: 'mono' }, e.branch),
          e.ticket ? ' [' + e.ticket + ']' : '', e.line ? h('span', { class: 'meta' }, ' ' + e.line) : '',
          e.pushed ? '' : h('span', { class: 'faint' }, ' nothing pushed')))
      }
    }
    for (const s of t.sessions || []) {             // teammates' branches, read from their commits
      const tm = s.team || {}
      const people = (tm.people || []).length ? tm.people.join(' and ') : 'You, on another machine'
      const files = Object.keys((s.changes && s.changes.files) || {})
      rows.push(h('li', {}, h('b', {}, people), ' ', h('span', { class: 'mono' }, s.branch),
        tm.ticket ? ' [' + tm.ticket + ']' : '', tm.line ? h('span', { class: 'meta' }, ' ' + tm.line) : '',
        files.length ? h('span', { class: 'faint' }, ' · ' + files.slice(0, 3).join(', ') +
          (files.length > 3 ? ' and ' + (files.length - 3) + ' more' : '')) : ''))
    }
    return h('div', { class: 'card pad stack', 'data-anim': 'g:team' },
      h('span', { class: 'meta' }, 'Team sync · ' + r.name),
      h('div', { class: 'row-flex' }, chip(sharing ? 'Sharing' : t.on ? 'Not sharing' : 'Off', sharing ? 'ok' : 'medium', true),
        h('span', { class: 'meta' }, pol.why ? pol.why : who)),
      pol.widened ? h('span', { class: 'meta' }, 'The policy allows more than you agreed to: orch team on to review it.') : null,
      held ? h('span', { class: 'faint' }, 'Withheld from your record in the last 24 h: ' + held) : null,
      rows.length ? h('ul', { class: 'list' }, rows) : h('span', { class: 'faint' }, 'No teammate’s record read yet'))
  })
}

// ------------------------------------------------------------------------------- activity
function feedItems () {
  const out = []
  for (const n of (ui.notes && ui.notes.notes) || []) {
    learn(ui.notes.sessions)
    out.push({ at: n.delivered || n.written || n.created, kind: 'note', icon: 'note', text: noteHeadline(n), note: n.text,
      href: '#/overlap/' + encodeURIComponent(n.finding_key), key: 'n:' + n.id })
  }
  for (const f of (ui.findings && ui.findings.findings) || []) {
    if (!inChosen(f.repository.common_dir) || (f.latent && !ui.showAll)) continue
    out.push({ at: f.first_seen, kind: 'overlap', icon: 'change', cls: f.strength === 'strong' ? 'strong' : '', text: overlapTitle(f),
      href: '#/overlap/' + encodeURIComponent(f.key), key: 'f:' + f.key })
    if (f.resolved) out.push({ at: f.resolved, kind: 'overlap', icon: 'check', text: 'Resolved: ' + overlapTitle(f), href: '#/overlap/' + encodeURIComponent(f.key), key: 'r:' + f.key })
  }
  for (const p of (ui.push && ui.push.push) || []) {
    out.push({ at: p.modified, kind: 'push', icon: 'push', text: 'git push' + (p.session ? ' from ' + N(p.session) : '') + ' went through with one line',
      note: p.line, key: 'p:' + p.name })
  }
  for (const r of repos()) {
    for (const s of r.sessions) {
      out.push({ at: s.started, kind: 'session', icon: 'play', text: sname(s) + ' started (' + agentName(s.agent) + (s.branch ? ', ' + s.branch : '') + ')',
        href: '#/session/' + encodeURIComponent(s.id), key: 's:' + s.id })
    }
  }
  return out.filter((it) => it.at).sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
}
function feedRow (it, withNote) {
  return h('div', { class: 'item', 'data-anim': 'feed:' + it.key },
    h('time', { datetime: it.at, title: new Date(it.at).toLocaleString() }, clock(it.at)),
    h('span', { class: 'kind ' + (it.cls || it.kind) }, icon(it.icon, 'sm')),
    h('div', { class: 'min0' }, it.href ? h('a', { href: it.href }, it.text) : h('span', {}, it.text),
      withNote && it.note ? h('div', { class: 'bubble' }, it.note) : null))
}
function activityView () {
  learn(sessionsById())
  learn(ui.findings && ui.findings.sessions)
  const filters = [['all', 'All'], ['note', 'Notes'], ['overlap', 'Overlaps'], ['push', 'Push lines'], ['session', 'Sessions']]
  const items = feedItems().filter((it) => ui.afilter === 'all' || it.kind === ui.afilter)
  return [pageHead('Activity', 'Everything Orchestraty noticed and said, newest first.'),
    h('div', { class: 'filters', role: 'group', 'aria-label': 'Show' }, filters.map(([id, text]) =>
      h('button', { type: 'button', class: 'filter', 'aria-pressed': String(ui.afilter === id), 'data-key': 'af:' + id,
        onclick: () => { ui.afilter = id; render() } }, text))),
    items.length ? h('div', { class: 'card feed' }, items.slice(0, 200).map((it) => feedRow(it, true)))
      : empty('Nothing to show', 'Notes, overlaps, push lines and sessions appear here as they happen.')]
}

// ------------------------------------------------------------------------------- projects
// Every project agents work in, git or not, each with its sessions; a click shows that one.
function projectsView () {
  const all = allRepos()
  const lead = 'Every project your agents work in, each with its own sessions. ' +
    (ui.status && ui.status.unwatched && ui.status.unwatched.length ? 'Some aren’t watched yet.' : '')
  const out = [pageHead('Projects', lead.trim())]
  if (!all.length) return [out, noRepos()]
  out.push(unwatchedCards(), h('div', { class: 'card rows' }, all.map(projectRow)))
  return out
}
function projectFolder (r) {
  return r.kind === 'folder' ? r.common_dir : r.common_dir.replace(/[\\/]\.git[\\/]?$/i, '')
}
function projectRow (r) {
  const live = r.sessions.filter((s) => s.state === 'live')
  const count = (k) => r.overlaps.filter((o) => o.strength === k).length
  const agents = {}
  for (const s of live) agents[agentName(s.agent)] = (agents[agentName(s.agent)] || 0) + 1
  return h('a', { class: 'row', href: '#/sessions', 'data-anim': 'prow:' + r.common_dir,
    onclick: () => { ui.repo = r.common_dir; store(REPO_KEY, r.common_dir); seen.clear() } },
  h('div', { class: 'row-flex min0' }, h('span', { class: 'av git', 'aria-hidden': 'true' }, (r.name || '?').slice(0, 1).toUpperCase()),
    h('div', { class: 'min0' }, h('div', { class: 'row-flex' }, h('b', {}, r.name),
      h('span', { class: 'meta' }, r.kind === 'folder' ? 'folder without git' : 'git repository')),
    h('div', { class: 'meta mono', title: projectFolder(r) }, middle(projectFolder(r), 64)))),
  h('div', { class: 'tags' },
    chip(live.length ? plural(live.length, 'session', 'sessions') + ': ' + Object.entries(agents).map(([k, v]) => v + ' ' + k).join(', ') : 'No live session'),
    count('strong') ? chip(plural(count('strong'), 'strong overlap', 'strong overlaps'), 'strong') : null,
    count('medium') ? chip(plural(count('medium'), 'medium overlap', 'medium overlaps'), 'medium') : null))
}

// ------------------------------------------------------------------------------- sessions
function sessionsView () {
  learn(sessionsById())
  const toggle = h('button', { type: 'button', class: 'btn sm quiet', 'data-key': 'show-all', onclick: () => setShowAll(!ui.showAll) },
    ui.showAll ? 'Hide ended sessions' : 'Show ended sessions')
  if (!allRepos().length) return [pageHead('Sessions', null, toggle), noRepos()]
  const all = repos().flatMap((r) => r.sessions)
  return [pageHead('Sessions', 'Every agent working on ' + repos().map((r) => r.name).join(', ') + ', each in its own worktree.', toggle),
    unwatchedCards(),
    all.length ? h('div', { class: 'card rows' }, all.map((s) => h('a', { class: 'row', href: '#/session/' + encodeURIComponent(s.id), 'data-anim': 'srow:' + s.id },
      h('div', { class: 'row-flex min0' }, avatar(s),
        h('div', { class: 'min0' }, h('div', { class: 'row-flex' }, h('span', { class: 'title' }, sname(s)),
          h('span', { class: 'meta' }, agentName(s.agent) + (s.branch ? ' · ' + s.branch : ''))), h('div', { class: 'meta' }, doing(s)))),
      h('div', { class: 'side-tags' }, stateChip(s), h('span', { class: 'faint nowrap' }, since(s.last_event)), icon('next')))))
      : [empty('No sessions', 'A session shows here as soon as an agent with the hooks starts in a watched project, or a worktree of a watched repository changes.' +
        (ui.showAll ? '' : ' Ended sessions are hidden: Show ended sessions lists them.')), connectionPanel()]]
}
function sessionView () {
  const d = ui.detail
  learn(sessionsById())
  if (!d) return [backTo('#/sessions', 'Sessions'), empty('Session not found', 'The record has nothing under this id. It may belong to an earlier state folder.')]
  learn(d.sessions)
  const s = d.session
  const files = Object.entries(d.changes.files || {})
  const symbols = d.changes.symbols || []
  const breaking = symbols.filter((c) => c.contract === 'breaking')
  const claimsHeld = ((sessionsById()[s.id] || {}).claims) || []
  const touching = repos().flatMap((r) => r.overlaps).filter((o) => (o.sessions || []).includes(s.id) || o.changer === s.id || o.receiver === s.id)
  const [, , stateText] = stateOf(s)
  const head = h('div', { class: 'row-flex', 'data-anim': 'shead' }, avatar(s, null, 'lg'),
    h('div', { class: 'grow min0' }, h('h1', { class: 'h1' }, sname(s)),
      h('p', { class: 'meta' }, agentName(s.agent) + (s.branch ? ' · branch ' + s.branch : '') + ' · ' + stateText)),
    s.state === 'live' && s.agent !== 'cloud' ? h('a', { class: 'btn', href: '#/claims?new=1&session=' + encodeURIComponent(s.id) }, 'New claim') : null,
    h('button', { type: 'button', class: 'btn', onclick: () => copyText('orch handoff --session ' + s.id.slice(0, 12)) }, 'Hand off…'))
  const out = [backTo('#/sessions', 'Sessions'), head,
    h('div', { class: 'grid-3' },
      h('div', { class: 'card stat' }, h('span', { class: 'meta' }, 'Files changed'), h('b', {}, String(files.length))),
      h('div', { class: 'card stat' }, h('span', { class: 'meta' }, 'Contracts changed'), h('b', {}, String(symbols.length)),
        h('span', { class: 'faint' }, breaking.length ? plural(breaking.length, 'breaking change', 'breaking changes') : 'none breaking')),
      h('div', { class: 'card stat' }, h('span', { class: 'meta' }, 'Notes it got'), h('b', {}, String(d.notes_received.length))))]
  out.push(h('div', { class: 'grid-2' },
    h('section', { class: 'card' }, h('div', { class: 'pad' }, h('h2', { class: 'h2' }, 'What it changed')),
      files.length || symbols.length ? h('div', { class: 'files' },
        symbols.map((c) => h('div', { class: 'file' },
          h('div', { class: 'min0' }, code(symName(c.symbol) + '()'), h('span', { class: 'meta' }, ' in ' + symFile(c.symbol)),
            c.reasons && c.reasons.length ? h('div', { class: 'meta' }, c.reasons.join('; ')) : null),
          chip(c.contract === 'breaking' ? 'breaking' : c.contract || c.kind, c.contract === 'breaking' ? 'strong' : ''))),
        files.filter(([p]) => !symbols.some((c) => symFile(c.symbol) === p)).map(([p, st]) => h('div', { class: 'file' },
          h('div', { class: 'min0' }, code(p)), chip({ A: 'added', M: 'modified', D: 'deleted' }[st] || st))))
        : h('p', { class: 'meta pad' }, 'Nothing changed since its base commit.')),
    h('section', { class: 'card' }, h('div', { class: 'pad' }, h('h2', { class: 'h2' }, 'What touches it')),
      touching.length ? h('div', { class: 'rows' }, touching.map((o) => h('a', { class: 'row', href: '#/overlap/' + encodeURIComponent(o.key) },
        h('div', { class: 'min0' }, h('b', {}, overlapTitle(o)), h('div', { class: 'meta' }, overlapWhere(o))), strengthChip(o.strength))))
        : h('p', { class: 'meta pad' }, 'No other session’s work touches this one.'))))
  if (claimsHeld.length) {
    out.push(h('section', { class: 'section' }, sectionHead('Its claims', claimsHeld.length),
      h('div', { class: 'card rows' }, claimsHeld.map((c) => claimRow(c, s)))))
  }
  out.push(h('section', { class: 'section' }, sectionHead('Notes it got', d.notes_received.length),
    d.notes_received.length ? d.notes_received.map((n) => noteCard(n)) : empty('No notes', 'Nothing another session did has concerned this one so far.')))
  if (d.notes_caused.length) {
    out.push(h('section', { class: 'section' }, sectionHead('Notes its changes caused', d.notes_caused.length),
      h('div', { class: 'card feed' }, d.notes_caused.map((n) => h('div', { class: 'item' },
        h('time', {}, clock(n.delivered || n.created)), h('span', { class: 'kind note' }, icon('note', 'sm')),
        h('a', { href: '#/overlap/' + encodeURIComponent(n.finding_key) }, noteHeadline(n)))))))
  }
  if (d.push) out.push(h('section', { class: 'section' }, sectionHead('Its push line'), h('pre', { class: 'term' }, d.push.line)))
  out.push(h('details', { 'data-key': 'sess-more' }, h('summary', {}, icon('next', 'sm'), 'Details for experts'),
    h('dl', { class: 'facts card', style: null },
      h('dt', {}, 'Session id'), h('dd', {}, code(s.id)), h('dt', {}, 'Worktree'), h('dd', {}, code(s.worktree || '—')),
      h('dt', {}, 'Base commit'), h('dd', {}, code(s.base_commit || '—'), s.base_guessed ? ' (guessed)' : ''),
      h('dt', {}, 'Started'), h('dd', {}, new Date(s.started).toLocaleString()), h('dt', {}, 'Last event'), h('dd', {}, since(s.last_event)),
      h('dt', {}, 'Last events'), h('dd', {}, d.events.slice(0, 12).map((e) => h('div', { class: 'mono' },
        stamp(e.ts) + '  ' + e.event + (e.tool_name ? ' ' + e.tool_name : '') + (e.file_path ? ' ' + relPath(s.worktree, e.file_path) : '')))))))
  return out
}
function noteCard (n) {
  return h('article', { class: 'card pad stack', 'data-anim': 'note:' + n.id },
    h('div', { class: 'row-flex' }, chip(n.state === 'delivered' ? 'Delivered' : n.state === 'withdrawn' ? 'Taken back' : 'Waiting', n.state === 'delivered' ? 'brand' : ''),
      h('span', { class: 'meta' }, noteWhen(n)), h('span', { class: 'faint' }, n.delivered ? clock(n.delivered) : clock(n.created)),
      n.time_to_awareness_ms !== null ? h('span', { class: 'faint' }, msText(n.time_to_awareness_ms) + ' after the change') : null,
      h('span', { class: 'grow' }), h('a', { class: 'btn sm quiet', href: '#/overlap/' + encodeURIComponent(n.finding_key) }, 'The overlap')),
    h('div', { class: 'bubble' }, n.text))
}

// ------------------------------------------------------------------------------- overlaps
function overlapsView () {
  learn(sessionsById())
  learn(ui.findings && ui.findings.sessions)
  if (!allRepos().length) return [pageHead('Overlaps'), noRepos()]
  const open = openFindings()
  const filters = [['all', 'All'], ['strong', 'Strong'], ['medium', 'Medium'], ['weak', 'Weak']]
  const out = [pageHead('Overlaps', 'Where one session’s changes touch another’s work, strongest first.'),
    h('div', { class: 'row-flex' }, h('div', { class: 'filters', role: 'group', 'aria-label': 'Strength' }, filters.map(([id, text]) =>
      h('button', { type: 'button', class: 'filter', 'aria-pressed': String(ui.ofilter === id), 'data-key': 'of:' + id,
        onclick: () => { ui.ofilter = id; render() } }, text))),
    h('span', { class: 'grow' }),
    h('button', { type: 'button', class: 'btn sm quiet', 'data-key': 'show-latent', onclick: () => setShowAll(!ui.showAll) },
      ui.showAll ? 'Hide not-yet-touched files' : 'Show not-yet-touched files'))]
  const groups = STRENGTHS.filter((k) => ui.ofilter === 'all' || ui.ofilter === k)
  let any = false
  for (const k of groups) {
    const list = open.filter((f) => f.strength === k)
    if (!list.length) continue
    any = true
    out.push(h('section', { class: 'section' },
      h('div', { class: 'section-head' }, strengthChip(k), h('span', { class: 'meta' }, STRENGTH_WORDS[k][1])),
      h('div', { class: 'card rows' }, list.map(overlapRow))))
  }
  if (!any) out.push(empty('Nothing overlaps', 'Your agents’ work doesn’t touch right now.'))
  const resolved = ((ui.findings && ui.findings.findings) || []).filter((f) => f.state === 'resolved' && inChosen(f.repository.common_dir))
  if (resolved.length) {
    out.push(h('button', { type: 'button', class: 'btn quiet back', 'data-key': 'show-resolved', onclick: () => { ui.showResolved = !ui.showResolved; render() } },
      ui.showResolved ? 'Hide resolved overlaps' : plural(resolved.length, 'resolved overlap', 'resolved overlaps') + ': show them'))
    if (ui.showResolved) out.push(h('div', { class: 'card rows' }, resolved.slice(0, 100).map(overlapRow)))
  }
  return out
}
function overlapRow (f) {
  const [told, cls] = f.state === 'resolved' ? ['Resolved', 'ok'] : toldOf(f)
  return h('a', { class: 'row', href: '#/overlap/' + encodeURIComponent(f.key), 'data-anim': 'orow:' + f.key },
    h('div', { class: 'min0' }, h('div', { class: 'title' }, overlapTitle(f)), h('div', { class: 'meta' }, overlapWhere(f))),
    h('div', { class: 'side-tags' }, chip(told, cls), h('span', { class: 'faint nowrap' }, since(f.state === 'resolved' ? f.resolved : f.first_seen)), icon('next')))
}
function overlapView () {
  const d = ui.detail
  learn(sessionsById())
  if (!d) return [backTo('#/overlaps', 'Overlaps'), empty('Overlap not found', 'It may have ended, or belong to an earlier state folder.')]
  learn(d.sessions)
  const f = d.finding
  const det = f.detail || {}
  const [told, tcls] = f.state === 'resolved' ? ['Resolved', 'ok'] : toldOf(f)
  const out = [backTo('#/overlaps', 'Overlaps'),
    h('div', { 'data-anim': 'ohead' }, h('div', { class: 'row-flex' }, strengthChip(f.strength), chip(f.state === 'open' ? 'Open' : 'Resolved', f.state === 'open' ? '' : 'ok'),
      chip(told, tcls), f.latent ? chip('Not editing that file yet', 'medium') : null),
    h('h1', { class: 'h1', style: null }, overlapTitle(f)), h('p', { class: 'lead' }, consequence(f))),
    h('section', { class: 'card pad', 'data-anim': 'oflow' }, overlapFlow(f))]
  const what = []
  if (det.reasons && det.reasons.length) what.push(h('ul', { class: 'meta' }, det.reasons.map((r) => h('li', {}, r))))
  if (f.rule === 'O3' || f.rule === 'O4' || f.rule === 'O6') {
    const rows = f.rule === 'O6' ? (det.left || []).map((c) => [c.symbol, c.file || symFile(c.symbol), c.lines]) : (det.uses || []).map((u) => [u.symbol, symFile(u.symbol), u.lines])
    what.push(h('div', { class: 'files' }, rows.map(([sym, file, lines]) => h('div', { class: 'file' },
      h('div', { class: 'min0' }, code(symName(sym) + '()'), h('span', { class: 'meta' }, ' in ' + file)), h('span', { class: 'faint' }, (lines || []).length ? 'line ' + lines.join(', ') : '')))))
  }
  if (f.rule === 'O5' && det.changes) {
    what.push(h('div', { class: 'files' }, Object.entries(det.changes).map(([sid, c]) => h('div', { class: 'file' },
      h('div', {}, h('b', {}, N(sid)), h('span', { class: 'meta' }, ' ' + (c.kind === 'changed' ? 'changed it' + (c.to ? ' to ' + c.to : c.from ? ' from ' + c.from : '') : c.kind))),
      h('span', { class: 'faint' }, c.file + ':' + c.line)))))
  }
  if (f.rule === 'O2' && det.kinds) {
    what.push(h('div', { class: 'files' }, Object.entries(det.kinds).map(([sid, kind]) => h('div', { class: 'file' }, h('b', {}, N(sid)), h('span', { class: 'meta' }, kind)))))
  }
  const noted = f.notes || []
  out.push(h('div', { class: 'grid-2' },
    h('section', { class: 'card pad stack', 'data-anim': 'owhat' }, h('h2', { class: 'h2' }, f.rule === 'O6' ? 'Who is left behind' : 'What changed'),
      what.length ? what : h('p', { class: 'meta' }, overlapWhere(f))),
    h('section', { class: 'card pad stack', 'data-anim': 'otold' }, h('h2', { class: 'h2' }, 'What the agents were told'),
      noted.length ? noted.slice(0, 3).map((n) => h('div', { class: 'stack' },
        h('div', { class: 'meta' }, 'To ' + N(n.session_id) + ', ' + noteWhen(n) + (n.delivered ? ' · delivered ' + clock(n.delivered) : n.state === 'delivered' ? ' · delivered' : ' · ' + n.state)),
        h('div', { class: 'bubble' }, n.text)))
        : h('p', { class: 'meta' }, f.rule === 'O1' ? 'Nobody: two sessions in one file only shows here and in orch status.' : 'No note was made for it.'))))
  const steps = [[f.first_seen, 'Orchestraty saw it: ' + overlapTitle(f), 'strong']]
  for (const n of noted) {
    if (n.written) steps.push([n.written, 'A note was ready for ' + N(n.session_id) + ', ' + noteWhen(n), 'brand'])
    if (n.delivered) steps.push([n.delivered, N(n.session_id) + ' got it', 'brand'])
  }
  if (f.resolved) steps.push([f.resolved, 'Resolved: it no longer holds', 'ok'])
  steps.sort((a, b) => Date.parse(a[0]) - Date.parse(b[0]))
  out.push(h('section', { class: 'card pad', 'data-anim': 'osteps' }, h('h2', { class: 'h2' }, 'What happened'),
    h('div', { class: 'steps' }, steps.map(([at, text, cls]) => h('div', { class: 'step' }, h('span', { class: 'pip ' + cls }), h('div', {}, text), h('time', {}, stamp(at)))))))
  const current = ui.labels[f.key] || (f.label && f.label.verdict) || null
  out.push(h('section', { class: 'card pad verdict', 'data-anim': 'overdict' },
    h('div', { class: 'grow' }, h('h2', { class: 'h2' }, 'Was this right?'),
      h('p', { class: 'meta' }, 'Your answer tunes what Orchestraty speaks up about. The same as orch label.')),
    h('div', { class: 'seg', role: 'group', 'aria-label': 'Your verdict' }, VERDICTS.map(([v, text]) =>
      h('button', { type: 'button', 'aria-pressed': String(current === v), 'data-key': 'verdict:' + v, onclick: () => sendLabel(f, v, current) }, text)))))
  const actions = [h('button', { type: 'button', class: 'btn', onclick: () => copyText('orch status') }, icon('copy', 'sm'), 'Copy orch status')]
  const receiver = (f.rule === 'O3' || f.rule === 'O4') && known[f.receiver]
  if (receiver && receiver.state === 'live' && f.file) {
    actions.unshift(h('button', { type: 'button', class: 'btn', onclick: () => makeClaim({ session: f.receiver, target: f.file }) },
      'Claim ' + baseName(f.file) + ' for ' + N(f.receiver)))
  }
  out.push(h('div', { class: 'row-flex' }, actions))
  out.push(h('details', { 'data-key': 'ov-more' }, h('summary', {}, icon('next', 'sm'), 'Details for experts'),
    h('p', { class: 'expert' }, 'rule ' + f.rule + ' (' + (RULE_NAMES[f.rule] || '') + ') · ' + f.strength + ' · first seen ' + stamp(f.first_seen) +
      ' · last seen ' + stamp(f.last_seen) + (f.latent ? ' · latent' : '') + ' · key ' + f.key)))
  return out
}

// ------------------------------------------------------------------------------- claims
function describeClaim (c) {
  if (c.kind === 'symbol') return symName(c.target) + '() in ' + c.file
  if (c.kind === 'area') return 'the folder ' + c.target + '/'
  return c.target
}
function claimRow (c, s) {
  return h('div', { class: 'row', 'data-anim': 'claim:' + c.id },
    h('div', { class: 'row-flex min0' }, s ? avatar(s, null, 'sm') : null,
      h('div', { class: 'min0' }, h('div', { class: 'title mono' }, describeClaim(c)),
        h('div', { class: 'meta' }, (s ? 'for ' + sname(s) + ' · ' : '') + 'until ' + clock(c.until) + ' · ' + (c.hard ? 'hard: an edit there waits for its user' : 'soft: others hear before they edit')))),
    h('button', { type: 'button', class: 'btn sm', 'data-key': 'release:' + c.id, onclick: () => releaseClaim(c) }, 'Release'))
}
function claimsView () {
  learn(sessionsById())
  if (!allRepos().length) return [pageHead('Claims'), noRepos()]
  const params = (ui.route && ui.route.params) || new URLSearchParams()
  if (params.get('new') === '1' && !ui.claimOpenSeen) { ui.claimOpen = true; ui.claimOpenSeen = true }
  const rows = repos().flatMap((r) => r.sessions.flatMap((s) => (s.claims || []).map((c) => [c, s])))
  const live = liveSessions().filter((s) => s.agent !== 'cloud' && s.agent !== 'git')
  const out = [pageHead('Claims', 'Files, folders and functions reserved for one session. The others hear before they edit them.',
    h('button', { type: 'button', class: 'btn' + (ui.claimOpen || tourLeads() ? '' : ' primary'), 'data-key': 'new-claim', onclick: () => { ui.claimOpen = !ui.claimOpen; render() } }, 'New claim', arrowHead('btn-arrow')))]
  if (ui.claimOpen) {
    const pick = params.get('session')
    const sel = h('select', { id: 'claim-session', 'data-key': 'claim-session', 'data-keep': '' },
      live.map((s) => h('option', { value: s.id, selected: s.id === pick ? 'selected' : null }, sname(s) + ' · ' + agentName(s.agent))))
    const target = h('input', { id: 'claim-target', type: 'text', placeholder: 'checkout.py, src/billing/ or payments.py::charge', 'data-key': 'claim-target', 'data-keep': '', autocomplete: 'off' })
    const minutes = h('select', { id: 'claim-minutes', 'data-key': 'claim-minutes', 'data-keep': '' },
      [[30, '30 minutes'], [60, '1 hour'], [120, '2 hours'], [240, '4 hours'], [480, '8 hours']].map(([v, t]) => h('option', { value: String(v), selected: v === 120 ? 'selected' : null }, t)))
    const hard = h('input', { id: 'claim-hard', type: 'checkbox', 'data-key': 'claim-hard', 'data-keep': '' })
    const submit = async (event) => {
      event.preventDefault()
      if (!target.value.trim()) { target.focus(); return toast('Say what to claim: a file, a folder, or file.py::function') }
      await makeClaim({ session: sel.value, target: target.value.trim(), minutes: Number(minutes.value), hard: hard.checked })
    }
    out.push(live.length ? h('form', { class: 'card pad form', 'data-anim': 'claimform', onsubmit: submit },
      h('h2', { class: 'h2' }, 'New claim'),
      h('div', { class: 'grid-3' },
        h('label', { class: 'field', for: 'claim-session' }, 'For session', sel),
        h('label', { class: 'field', for: 'claim-target' }, 'File, folder or function', target),
        h('label', { class: 'field', for: 'claim-minutes' }, 'For', minutes)),
      h('label', { class: 'check', for: 'claim-hard' }, hard, 'Hard: another session’s edit there waits for its user’s answer (Claude Code)'),
      ui.claimResult ? h('p', { class: 'result' + (ui.claimResult.bad ? ' bad' : '') }, ui.claimResult.text) : null,
      h('div', { class: 'row-flex' }, h('button', { type: 'submit', class: 'btn' + (tourLeads() ? '' : ' primary'), 'aria-disabled': ui.busy ? 'true' : null }, 'Claim it', arrowHead('btn-arrow')),
        h('button', { type: 'button', class: 'btn quiet', onclick: () => { ui.claimOpen = false; ui.claimResult = null; render() } }, 'Cancel')))
      : empty('No live session to claim for', 'A claim belongs to a session with hooks. Start an agent in a watched repository first.'))
  }
  out.push(rows.length ? h('div', { class: 'card rows' }, rows.map(([c, s]) => claimRow(c, s)))
    : empty('No claims hold right now', 'Claim a file, folder or function for a session, and the others hear before they edit it.'))
  return out
}

// ------------------------------------------------------------------------------- merge order
function mergeView () {
  learn(sessionsById())
  if (!allRepos().length) return [pageHead('Merge order'), noRepos()]
  const out = [pageHead('Merge order', 'The order that keeps every branch working when it lands.',
    h('button', { type: 'button', class: 'btn', onclick: () => copyText('orch merge-order') }, icon('copy', 'sm'), 'Copy orch merge-order'))]
  let any = false
  for (const r of repos()) {
    if (!r.merge_order.length) continue
    any = true
    out.push(h('section', { class: 'section' }, sectionHead(r.name),
      r.merge_order.map((m, i) => {
        const [a, b] = m.sessions
        const titleText = m.first ? 'Merge ' + N(m.first) + ' first' : N(a) + ' and ' + N(b) + ' depend on each other'
        return h('div', { class: 'card order', 'data-anim': 'm:' + r.common_dir + ':' + i },
          h('span', { class: 'n' }, String(i + 1)),
          h('div', { class: 'min0' }, h('b', {}, titleText), h('div', { class: 'meta' }, m.line)),
          chip(m.first ? 'First' : 'Together', m.first ? 'ok' : 'medium'))
      })))
  }
  if (!any) out.push(empty('Nothing to order', 'No session uses a change another session made, so any order works.'))
  return out
}

// ------------------------------------------------------------------------------- settings
function settingsView () {
  const cfg = ui.config
  const health = ui.health
  const out = [pageHead('Settings', 'How Orchestraty behaves on this computer.'), connectionPanel()]
  if (!cfg) return out
  const sw = (name, title, text) => {
    const on = !!cfg[name]
    return h('div', { class: 'setting' }, h('div', {}, h('b', { id: 'sw-' + name }, title), h('p', { class: 'meta' }, text)),
      h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(on), 'aria-labelledby': 'sw-' + name, 'data-key': 'switch-' + name,
        'aria-disabled': ui.busy ? 'true' : null, onclick: () => flip(name, !on) }))
  }
  out.push(h('section', { class: 'section' }, h('h2', { class: 'h2' }, 'Notes'),
    h('div', { class: 'card' },
      sw('steer', 'Steer', 'Stop an edit when a strong note arrives before it, so the agent re-plans before it builds on the old form.'),
      sw('wake', 'Wake', 'Wake a session that is waiting for its user when a strong note is about its work.'))))
  const st = new Map(allRepos().map((r) => [r.common_dir, r]))
  if (cfg.repos.length) {
    out.push(h('section', { class: 'section' }, h('h2', { class: 'h2' }, 'Extras, per repository'),
      cfg.repos.map((r) => {
        const s = st.get(r.common_dir) || {}
        const folder = r.folder.indexOf(' ') >= 0 ? '"' + r.folder + '"' : r.folder
        const line = (title, on, text, cmd) => h('div', { class: 'setting' },
          h('div', { class: 'min0' }, h('b', {}, title), h('p', { class: 'meta' }, text), command(cmd)), chip(on ? 'On' : 'Off', on ? 'ok' : ''))
        return h('div', { class: 'card' },
          h('div', { class: 'setting' }, h('div', {}, h('b', {}, r.name), h('p', { class: 'meta mono', title: r.folder }, middle(r.folder, 64))), h('span')),
          line('TypeScript contracts', s.typescript && s.typescript.on, 'Compare the exported API of .ts files too. Its notes say they are heuristic.',
            'orch ts ' + (s.typescript && s.typescript.on ? 'off' : 'on') + ' --repo ' + folder),
          line('Together check', s.together && s.together.on, s.together && s.together.on && s.together.command
            ? 'Runs “' + s.together.command + '” on two overlapping sessions’ work merged.'
            : s.together && s.together.command
              ? 'Off. When on, runs “' + s.together.command + '” on two overlapping sessions’ work merged.'
              : 'Run your tests on two overlapping sessions’ work merged, and tell both if they fail together. Set the command, then run orch together on.',
          s.together && s.together.on ? 'orch together off --repo ' + folder
            : s.together && s.together.command ? 'orch together on --repo ' + folder
              : 'orch together set "<your test command>" --repo ' + folder),
          line('Cloud agents’ branches', s.cloud && s.cloud.on, 'Fetch cloud agents’ branches read-only and compare them. The one use of the network; it asks first.',
            s.cloud && s.cloud.on ? 'orch remote off --repo ' + folder : 'orch remote on --repo ' + folder),
          line('Team sync', s.team && s.team.on, 'Share a closed record of your sessions with teammates through git, as the repository’s policy allows. It asks first.',
            s.team && s.team.on ? 'orch team off --repo ' + folder : 'orch team on --repo ' + folder))
      })))
  }
  out.push(h('section', { class: 'section' }, h('h2', { class: 'h2' }, 'Agents'),
    h('div', { class: 'card' },
      h('div', { class: 'setting' }, h('div', { class: 'min0' }, h('b', {}, 'Claude Code'),
        h('p', { class: 'meta' }, cfg.settings ? 'Hooks installed in ' + cfg.settings : demoOn() ? 'The demo writes no Claude Code settings.' : 'Not set up yet.'),
        cfg.settings ? null : command('orch init')), chip(cfg.settings ? 'Connected' : 'Not set up', cfg.settings ? 'ok' : '', !!cfg.settings)),
      h('div', { class: 'setting' }, h('div', { class: 'min0' }, h('b', {}, 'Codex'),
        h('p', { class: 'meta' }, 'Built, in testing. Without it, its changes still count through git.'), command('orch init --agent codex')), h('span')),
      h('div', { class: 'setting' }, h('div', { class: 'min0' }, h('b', {}, 'Antigravity'),
        h('p', { class: 'meta' }, 'Built, in testing. Without it, its changes still count through git.'), command('orch init --agent antigravity')), h('span')))))
  out.push(h('section', { class: 'section' }, h('h2', { class: 'h2' }, 'Projects'),
    h('div', { class: 'card' },
      sw('track', 'Track every project', 'Follow every project your agents work in, with or without git, on this computer only. Off: only the ones you watch.'),
      cfg.repos.map((r) => h('div', { class: 'setting' }, h('div', { class: 'min0' }, h('b', {}, r.name), h('p', { class: 'meta mono', title: r.folder }, middle(r.folder, 64)),
        h('details', { 'data-key': 'unwatch:' + r.common_dir }, h('summary', {}, icon('next', 'sm'), 'Stop watching it'), command(r.unwatch))), h('span'))),
      h('div', { class: 'setting' }, h('div', { class: 'min0' }, h('b', {}, 'Watch another repository'),
        h('p', { class: 'meta' }, 'Run this inside it, a git repository or a folder without git; Orchestraty compares the sessions in it from then on.'), command('orch watch')), h('span')))))
  const c = health && health.coordinator
  const doc = ui.doctor
  out.push(h('section', { class: 'section' }, h('h2', { class: 'h2' }, 'Health'),
    h('div', { class: 'card pad stack' },
      h('div', { class: 'row-flex' }, h('div', { class: 'grow' }, h('b', {}, 'Check everything Orchestraty needs'),
        h('p', { class: 'meta' }, 'The same checks as orch doctor, each with its fix.')),
      h('button', { type: 'button', class: 'btn', 'data-key': 'doctor', 'aria-disabled': ui.doctorBusy ? 'true' : null, onclick: runDoctor },
        ui.doctorBusy ? 'Checking…' : 'Run checks')),
      doc ? h('div', { class: 'stack' }, doc.checks.map((x) => h('div', { class: 'check-line' },
        chip(x.kind === 'ok' ? 'OK' : x.kind === 'fix' ? 'Fix' : 'Note', x.kind === 'ok' ? 'ok' : x.kind === 'fix' ? 'strong' : ''),
        h('div', { class: 'min0' }, h('div', {}, x.text), x.fix ? h('div', { class: 'fix' }, x.fix) : null))),
      h('p', { class: doc.problems ? 'meta' : 'meta' }, doc.lines.join(' '))) : null)))
  const choice = themeChoice()
  out.push(h('section', { class: 'section' }, h('h2', { class: 'h2' }, 'Appearance'),
    h('div', { class: 'card' }, h('div', { class: 'setting' }, h('div', {}, h('b', {}, 'Theme'), h('p', { class: 'meta' }, 'Follows your system unless you pick one.')),
      h('div', { class: 'seg', role: 'group', 'aria-label': 'Theme' }, [['system', 'System'], ['dark', 'Dark'], ['light', 'Light']].map(([v, t]) =>
        h('button', { type: 'button', 'aria-pressed': String(choice === v), 'data-key': 'theme:' + v, onclick: () => { setTheme(v); render() } }, t)))))))
  out.push(h('section', { class: 'section' }, h('h2', { class: 'h2' }, 'About'),
    h('dl', { class: 'card facts' },
      h('dt', {}, 'Version'), h('dd', {}, code(((ui.status && ui.status.ui) || {}).version || '?')),
      h('dt', {}, 'Coordinator'), h('dd', {}, c ? (c.running ? 'running since ' + clock(c.since) + ', version ' + (c.version || '?') + ', pid ' + (c.pid || '?') : 'not running: it starts with the next agent event') : '—'),
      h('dt', {}, 'State folder'), h('dd', { title: cfg.state }, code(middle(cfg.state, 70))),
      h('dt', {}, 'Record'), h('dd', {}, health ? (health.record.exists ? code(middle(health.record.path, 70)) : 'not made yet: the coordinator makes it') : '—'),
      h('dt', {}, 'Remove'), h('dd', {}, command(cfg.commands.restore), h('p', { class: 'meta' }, 'Takes the hooks out of Claude Code; orch uninstall removes everything.')))))
  return out
}

// ------------------------------------------------------------------------------- changes
async function act (url, body) {
  if (ui.busy) return null
  ui.busy = true
  try {
    return await post(url, body)
  } catch (err) {
    if (err instanceof AccessError) { denied(); return null }
    toast(String(err.message || err))
    return null
  } finally {
    ui.busy = false
  }
}
async function sendLabel (f, verdict, before) {
  ui.labels[f.key] = verdict
  render()
  const res = await act('/api/label', { finding: f.key, verdict })
  if (!res) { delete ui.labels[f.key]; return render() }
  const word = VERDICTS.find(([v]) => v === verdict)[1].toLowerCase()
  toast('Marked ' + word + '. Thanks: it tunes what Orchestraty speaks up about.', before && before !== verdict ? () => sendLabel(f, before, verdict) : null)
  announce('Marked ' + word)
}
async function makeClaim (body) {
  const res = await act('/api/claims', body)
  if (!res) return
  ui.claimResult = null
  ui.claimOpen = false
  toast(res.lines.join(' '))
  if (parseRoute().name !== 'claims') go('#/claims')
  else refresh()
}
async function releaseClaim (c) {
  const res = await act('/api/release', { id: c.id })
  if (res) { toast(res.lines.join(' ')); refresh() }
}
async function flip (name, on) {
  const res = await act('/api/config', { [name]: on ? 'on' : 'off' })
  if (!res) return
  const text = (res.lines || []).concat(res.error ? [res.error] : []).join(' ')
  toast(text || (name + (on ? ' is on.' : ' is off.')), res.error ? null : () => flip(name, !on))
  refresh()
}
// Refresh: the striped bar runs and the message stays while the coordinator looks at everything
// again; once it says that pass is done, the dashboard reloads and the message says what it found.
const RESCAN_POLL_MS = 400
const RESCAN_WAIT_MS = 20000
const RESCAN_MIN_MS = 900                 // the bar shows long enough to be seen
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
async function rescanned (id) {
  const res = await fetch('/api/rescan?id=' + encodeURIComponent(id), { cache: 'no-store', credentials: 'same-origin' })
  if (res.status === 403) throw new AccessError()
  if (!res.ok) throw new Error('HTTP ' + res.status)
  return res.json()
}
function refreshing (on) {
  const bar = $('refresh-bar')
  const btn = $('refresh-btn')
  btn.classList.toggle('is-busy', on)
  btn.setAttribute('aria-busy', on ? 'true' : 'false')
  bar.classList.toggle('is-done', !on)
  if (on) bar.hidden = false
  else setTimeout(() => { if (!ui.rescanning) bar.hidden = true }, 320)
}
const NOT_RUNNING = 'The background process isn’t running: it starts with the next agent event and looks at everything then.'
function rescanSummary (answer, running) {
  if (!running) return NOT_RUNNING
  if (!answer) return 'Still looking: new sessions show here as they’re found.'
  const all = allRepos()
  const live = all.reduce((n, r) => n + r.sessions.filter((s) => s.state === 'live').length, 0)
  return 'Refreshed: ' + plural(all.length, 'project', 'projects') + ', ' + plural(live, 'live session', 'live sessions') + '. ' +
    (answer.found ? plural(answer.found, 'new session', 'new sessions') + ' found.' : 'Nothing new found.')
}
async function rescan () {
  if (ui.rescanning) return
  const res = await act('/api/rescan', {})
  if (!res) return
  ui.rescanning = true
  const started = Date.now()
  refreshing(true)
  toast(res.lines.join(' '), null, true)
  refresh()
  let answer = null
  let running = res.running
  try {
    while (running && Date.now() - started < RESCAN_WAIT_MS) {
      await sleep(RESCAN_POLL_MS)
      const got = await rescanned(res.id)
      if (got.done) { answer = got; break }
      running = got.running
    }
  } catch (err) {
    if (err instanceof AccessError) { ui.rescanning = false; refreshing(false); return denied() }
  }
  ui.dirty = true
  await poll()
  const left = RESCAN_MIN_MS - (Date.now() - started)
  if (left > 0) await sleep(left)
  ui.rescanning = false
  refreshing(false)
  toast(rescanSummary(answer, running))
}
async function watchRepo (r) {
  const res = await act('/api/watch', { common_dir: r.common_dir })
  if (res) toast(res.lines.join(' '))
  refresh()
}
async function runDoctor () {
  if (ui.doctorBusy) return
  ui.doctorBusy = true
  if (parseRoute().name === 'settings') render()
  try {
    ui.doctor = await post('/api/doctor', {})
    announce(ui.doctor.lines.join(' '))
  } catch (err) {
    if (err instanceof AccessError) return denied()
    ui.doctor = { checks: [], problems: 1, lines: ['The checks did not run: ' + (err.message || err)] }
  } finally {
    ui.doctorBusy = false
  }
  if (parseRoute().name === 'settings') render()
}
function setShowAll (on) {
  ui.showAll = on
  store(ALL_KEY, on ? '1' : '0')
  refresh()
}

// ------------------------------------------------------------------------------- the frame
function drawSide (route) {
  const side = $('side')
  const name = route ? route.name : 'home'
  const current = { session: 'sessions', overlap: 'overlaps' }[name] || name
  const counts = { sessions: liveSessions().length, overlaps: strongCount(), claims: repos().reduce((n, r) => n + r.sessions.reduce((m, s) => m + (s.claims || []).length, 0), 0),
    merge: repos().reduce((n, r) => n + r.merge_order.length, 0) }
  const link = (id, text, ic, count, hot) => h('a', { class: 'nav', href: id === 'home' ? '#/' : '#/' + id, 'aria-current': current === id ? 'page' : null, title: text },
    icon(ic), h('span', { class: 'nav-label' }, text), count ? h('span', { class: 'count' + (hot ? ' hot' : '') }, String(count)) : null)
  side.replaceChildren(...PAGES.map(([id, text, ic]) => link(id, text, ic, counts[id], id === 'overlaps')),
    h('div', { class: 'side-fill' }), link('settings', 'Settings', 'gear'),
    h('p', { class: 'side-foot' }, 'Local only. Nothing leaves this computer.'))
}
function drawTop () {
  const pill = $('pill')
  const text = $('pill-text')
  const c = ui.status && ui.status.coordinator
  const needs = ui.status ? strongCount() : 0
  pill.classList.remove('is-need', 'is-warn', 'is-lost')
  if (ui.denied) { pill.classList.add('is-lost'); text.textContent = 'No access' }
  else if (ui.lost) { pill.classList.add('is-lost'); text.textContent = 'Can’t reach orch ui' }
  else if (!ui.status) text.textContent = 'Reading…'
  else if (needs) { pill.classList.add('is-need'); text.textContent = needs === 1 ? '1 needs you' : needs + ' need you' }
  else if (c && !c.running) { pill.classList.add('is-warn'); text.textContent = 'Coordinator stopped' }
  else text.textContent = 'All clear'
  $('demo-badge').hidden = !demoOn()
  const pick = $('repo-pick')
  const all = allRepos()
  if (all.length > 1) {
    const sel = h('select', { 'aria-label': 'Project', id: 'repo-select' },
      h('option', { value: '' }, 'All projects'), all.map((r) => h('option', { value: r.common_dir }, r.name)))
    sel.value = all.some((r) => r.common_dir === ui.repo) ? ui.repo : ''
    sel.addEventListener('change', () => { ui.repo = sel.value; store(REPO_KEY, sel.value); seen.clear(); refresh() })
    const shown = pick.querySelector('select')
    if (!shown || shown.options.length !== sel.options.length) pick.replaceChildren(sel)
    else if (shown.value !== sel.value) shown.value = sel.value        // a project picked elsewhere (Projects)
  } else if (all.length === 1) {
    if (pick.textContent !== all[0].name) pick.replaceChildren(h('span', { class: 'repo-name', title: all[0].common_dir }, icon('repo', 'sm'), all[0].name))
  } else pick.replaceChildren()
  if (!$('pop').hidden) drawPop()
}
function drawThemeButton () {
  const dark = darkNow()
  const b = $('theme-btn')
  if (!b) return
  b.replaceChildren(icon(dark ? 'sun' : 'moon'))
  b.setAttribute('aria-label', dark ? 'Switch to the light theme' : 'Switch to the dark theme')
  b.title = b.getAttribute('aria-label')
}

// ------------------------------------------------------------------------------- the status pop
function drawPop () {
  const pop = $('pop')
  const c = ui.status && ui.status.coordinator
  const needs = ui.status ? strongCount() : 0
  pop.replaceChildren(...[
    h('div', { class: 'line' }, h('span', { class: 'dot' + (c && c.running ? '' : ' warn') }), h('b', {}, c && c.running ? 'Coordinator running' : 'Coordinator stopped')),
    h('p', { class: 'meta' }, c && c.running ? 'Since ' + clock(c.since) + '. It watches ' + plural(liveSessions().length, 'live session', 'live sessions') + '. Nothing leaves this computer.'
      : 'It starts by itself with the next agent event, and stops when idle.'),
    h('div', { class: 'line' }, h('span', { class: 'dot' + (needs ? ' strong' : '') }), h('span', {}, needs ? plural(needs, 'strong overlap needs', 'strong overlaps need') + ' a look' : 'Nothing needs you')),
    ui.lost ? h('div', { class: 'line' }, h('span', { class: 'dot off' }), h('span', {}, 'This page can’t reach orch ui' + (ui.lastOk ? ' since ' + clock(ui.lastOk.toISOString()) : ''))) : null,
    h('p', { class: 'faint' }, ui.lastOk ? 'Updated ' + stamp(ui.lastOk.toISOString()) : ''),
    needs ? h('button', { type: 'button', class: 'btn sm', onclick: () => { closeLayers(); go('#/') } }, 'Show them') : null].filter(Boolean))
}
function togglePop () {
  const pop = $('pop')
  const open = pop.hidden
  closeLayers()
  if (open) {
    drawPop()
    pop.hidden = false
    $('pill').setAttribute('aria-expanded', 'true')
  }
}

// ------------------------------------------------------------------------------- toast
let toastTimer = null
function toast (text, undo, stay) {
  const el = $('toast')
  clearTimeout(toastTimer)
  el.replaceChildren(...[h('span', {}, text), undo ? h('button', { type: 'button', class: 'btn sm', onclick: () => { el.hidden = true; undo() } }, 'Undo') : null].filter(Boolean))
  el.hidden = false
  announce(text)
  if (!stay) toastTimer = setTimeout(() => { el.hidden = true }, undo ? 6000 : 4200)
}

// ------------------------------------------------------------------------------- the palette and help
let opener = null
function closeLayers () {
  const scrim = $('scrim')
  if (!scrim.hidden) {
    scrim.hidden = true
    scrim.replaceChildren()
    if (opener && opener.isConnected) opener.focus({ preventScroll: true })
  }
  $('pop').hidden = true
  $('pill').setAttribute('aria-expanded', 'false')
}
function openLayer (node) {
  opener = document.activeElement
  closeLayers()
  opener = opener || null
  const scrim = $('scrim')
  scrim.replaceChildren(node)
  scrim.hidden = false
}
function paletteItems () {
  learn(sessionsById())
  const items = PAGES.map(([id, text, ic]) => ({ text: 'Go to ' + text, group: 'Page', icon: ic, run: () => go(id === 'home' ? '#/' : '#/' + id) }))
  items.push({ text: 'Go to Settings', group: 'Page', icon: 'gear', run: () => go('#/settings') })
  for (const r of repos()) {
    for (const o of r.overlaps) {
      if (o.strength !== 'weak') items.push({ text: 'Review: ' + overlapTitle(o), group: 'Overlap', icon: 'overlap', run: () => go('#/overlap/' + encodeURIComponent(o.key)) })
    }
    for (const s of r.sessions) items.push({ text: 'Open session ' + sname(s), group: 'Session', icon: 'users', run: () => go('#/session/' + encodeURIComponent(s.id)) })
  }
  const steer = ui.config ? ui.config.steer : null
  items.push({ text: 'New claim…', group: 'Action', icon: 'lock', run: () => { ui.claimOpen = true; go('#/claims') } })
  items.push({ text: 'Release a claim…', group: 'Action', icon: 'lock', run: () => go('#/claims') })
  items.push({ text: 'Run health checks', group: 'Action', icon: 'gear', run: () => { go('#/settings'); runDoctor() } })
  items.push({ text: 'Refresh: look for new sessions now', group: 'Action', icon: 'refresh', run: rescan })
  items.push({ text: steer ? 'Turn steer off' : 'Turn steer on or off', group: 'Setting', icon: 'steer', run: () => go('#/settings') })
  items.push({ text: darkNow() ? 'Switch to the light theme' : 'Switch to the dark theme', group: 'Setting', icon: darkNow() ? 'sun' : 'moon', run: () => setTheme(darkNow() ? 'light' : 'dark') })
  items.push({ text: ui.showAll ? 'Hide ended sessions' : 'Show ended sessions', group: 'Setting', icon: 'users', run: () => setShowAll(!ui.showAll) })
  items.push({ text: 'Copy orch status', group: 'Command', icon: 'copy', run: () => copyText('orch status') })
  if (demoOn() && ui.demo && ui.demo.next !== null) items.push({ text: 'Demo: run the next step', group: 'Demo', icon: 'play', run: nextStep })
  return items
}
function openPalette () {
  const items = paletteItems()
  let shown = items
  let pick = 0
  const list = h('div', { class: 'list', role: 'listbox', id: 'pal-list', 'aria-label': 'Results' })
  const input = h('input', { type: 'search', placeholder: 'Search pages, overlaps, sessions and actions…', 'aria-label': 'Search', 'aria-controls': 'pal-list', autocomplete: 'off' })
  const run = (it) => { closeLayers(); it.run() }
  const draw = () => {
    const q = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
    shown = items.filter((it) => q.every((w) => (it.text + ' ' + it.group).toLowerCase().includes(w)))
    pick = Math.min(pick, Math.max(0, shown.length - 1))
    list.replaceChildren(...(shown.length ? shown.slice(0, 50).map((it, i) => h('button', { type: 'button', class: 'opt', role: 'option', 'aria-selected': String(i === pick), tabindex: '-1',
      onclick: () => run(it), onmousemove: () => { if (pick !== i) { pick = i; draw() } } }, icon(it.icon), h('span', { class: 'grow' }, it.text), h('span', { class: 'faint' }, it.group)))
      : [h('p', { class: 'meta pad' }, 'Nothing matches. Try “claim”, “steer” or the name of a session.')]))
    const on = list.querySelector('[aria-selected="true"]')
    if (on) on.scrollIntoView({ block: 'nearest' })
  }
  input.addEventListener('input', () => { pick = 0; draw() })
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); pick = Math.min(pick + 1, shown.length - 1); draw() }
    if (e.key === 'ArrowUp') { e.preventDefault(); pick = Math.max(pick - 1, 0); draw() }
    if (e.key === 'Enter' && shown[pick]) { e.preventDefault(); run(shown[pick]) }
  })
  openLayer(h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Search or run' }, input, list,
    h('div', { class: 'foot' }, h('span', {}, h('kbd', { class: 'kbd' }, '↑↓'), ' move'), h('span', {}, h('kbd', { class: 'kbd' }, 'Enter'), ' run'), h('span', {}, h('kbd', { class: 'kbd' }, 'Esc'), ' close'))))
  draw()
  input.focus()
}
function openHelp () {
  const terms = [['Session', 'One agent working in its own worktree: its own copy of the code.'], ['Overlap', 'One session’s change touches another session’s work.'],
    ['Strong', 'It would break when merged. The affected agent is told before its next edit.'], ['Medium', 'Both changed the same thing. Both agents are told once.'],
    ['Weak', 'Same file, different parts. Nobody is interrupted.'], ['Note', 'The short message an agent receives. Always from a template, never from an AI model.'],
    ['Claim', 'A file, folder or function reserved for one session.'], ['Push line', 'One line at git push about callers nobody updated. The push still goes through.'],
    ['Steer', 'Optional: a strong note stops the edit it arrives before, so the agent re-plans.']]
  const close = h('button', { type: 'button', class: 'icon-btn sm', 'aria-label': 'Close', onclick: closeLayers }, icon('close', 'sm'))
  openLayer(h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'help-title' },
    h('div', { class: 'help' }, h('div', { class: 'row-flex' }, h('h2', { id: 'help-title', class: 'grow' }, 'The words on this page'), close),
      h('dl', { class: 'glossary' }, terms.map(([t, d]) => [h('dt', {}, t), h('dd', {}, d)])),
      h('h3', {}, 'Shortcuts'),
      h('div', { class: 'keys' }, [['Ctrl K', 'search or run'], ['/', 'search'], ['G then H', 'home'], ['G then S', 'sessions'], ['G then O', 'overlaps'],
        ['G then P', 'projects'], ['G then A', 'activity'], ['G then C', 'claims'], ['G then M', 'merge order'], ['?', 'this help'], ['Esc', 'close']].map(([k, t]) =>
        h('span', {}, h('kbd', { class: 'kbd' }, k), ' ' + t))))))
  close.focus()
}

// ------------------------------------------------------------------------------- the demo tour
function drawTour () {
  const tour = $('tour')
  const d = ui.demo
  const root = document.documentElement
  if (!d) {
    tour.hidden = true
    root.style.setProperty('--tour-w', '0px')
    root.style.removeProperty('--tour-h')
    return
  }
  tour.hidden = false
  tour.classList.toggle('min', ui.tourMin)
  const done = d.steps.filter((s) => s.done)
  const last = done[done.length - 1]
  const n = d.steps.length
  const nextStepInfo = d.next === null ? null : d.steps[d.next - 1]
  const progress = h('div', { class: 'progress', 'aria-hidden': 'true' }, h('i'))
  progress.firstChild.style.setProperty('--p', Math.round((done.length / n) * 100) + '%')
  const minBtn = h('button', { type: 'button', class: 'icon-btn sm', 'data-key': 'tour-min', 'aria-label': ui.tourMin ? 'Show the tour' : 'Shrink the tour',
    onclick: () => { ui.tourMin = !ui.tourMin; store(TOUR_KEY, ui.tourMin ? '1' : '0'); drawTour() } }, icon(ui.tourMin ? 'plus' : 'minus', 'sm'))
  const head = h('div', { class: 'tour-head' },
    h('div', { class: 'row-flex' }, chip('Demo · ' + done.length + ' of ' + n, 'brand'), h('span', { class: 'grow' }), minBtn),
    h('h2', { id: 'tour-title', tabindex: '-1' }, last ? last.text : 'Two pretend sessions are ready'), progress)
  const outputs = last ? last.outputs.map((o, i) => o.fold
    ? h('details', { 'data-key': 'out-' + last.n + '-' + i }, h('summary', {}, icon('next', 'sm'), o.label), h('pre', { class: 'term' }, o.text))
    : h('div', { class: 'stack' }, h('span', { class: 'meta' }, o.label), h('pre', { class: 'term' }, o.text))) : []
  const body = ui.tourMin ? null : h('div', { class: 'tour-body' },
    last ? null : h('p', { class: 'meta' }, 'This sandbox runs the whole loop with two scripted Claude Code sessions, A and B, in a temporary folder. Press Next step and watch the page change.'),
    outputs,
    h('ol', { class: 'tour-steps' }, d.steps.map((s) => h('li', { class: s.done ? 'done' : s.n === d.next ? 'next' : '' },
      h('span', { class: 'n', 'aria-hidden': 'true' }, s.done ? icon('check', 'sm') : String(s.n)), h('span', {}, h('span', { class: 'sr-only' }, 'Step ' + s.n + (s.done ? ', done: ' : ': ')), s.text)))),
    h('p', { class: 'faint' }, 'In ' + middle(d.folder, 48) + ', removed when orch ui stops.'))
  const nextBtn = nextStepInfo ? h('button', { type: 'button', class: 'btn primary', id: 'demo-next', 'data-key': 'demo-next', 'aria-disabled': ui.demoBusy ? 'true' : null, onclick: nextStep },
    ui.demoBusy ? 'Running…' : 'Next step', arrowHead('btn-arrow')) : null
  const foot = h('div', { class: 'tour-foot' },
    h('span', { class: 'grow' }, ui.demoError || (nextStepInfo ? 'Next: ' + nextStepInfo.text : 'Done. Ctrl+C in the terminal stops orch ui and removes the sandbox.')), nextBtn)
  swap(tour, [head, body, foot])
  const wide = window.matchMedia('(min-width: 1101px)').matches
  root.style.setProperty('--tour-w', wide && !ui.tourMin ? '360px' : '0px')
  if (!wide || ui.tourMin) root.style.setProperty('--tour-h', tour.offsetHeight + 24 + 'px')
  else root.style.removeProperty('--tour-h')
}
async function nextStep () {
  if (ui.demoBusy || !ui.demo || ui.demo.next === null) return
  ui.demoBusy = true
  ui.demoError = null
  drawTour()
  try {
    const d = await post('/api/demo/next')
    cache.set('/api/demo', { etag: null, data: d })
    ui.demo = d
    const last = d.steps.filter((s) => s.done).pop()
    if (last) announce('Step ' + last.n + ' done: ' + last.text)
  } catch (err) {
    if (err instanceof AccessError) return denied()
    ui.demoError = 'The step did not run: ' + (err.message || err)
  }
  ui.demoBusy = false
  refresh()
}

// ------------------------------------------------------------------------------- start
let pendingG = 0
function typing (el) {
  return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
}
function onKey (event) {
  if (event.key === 'Escape') { closeLayers(); return }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); return }
  if (typing(event.target) || event.ctrlKey || event.metaKey || event.altKey || !$('scrim').hidden) return
  if (event.key === '/') { event.preventDefault(); openPalette(); return }
  if (event.key === '?') { event.preventDefault(); openHelp(); return }
  const k = event.key.toLowerCase()
  if (pendingG && Date.now() - pendingG < 1200) {
    pendingG = 0
    const page = PAGES.find((p) => p[3] === k)
    if (page) go(page[0] === 'home' ? '#/' : '#/' + page[0])
    else if (k === ',') go('#/settings')
    return
  }
  if (k === 'g') pendingG = Date.now()
}

document.addEventListener('DOMContentLoaded', () => {
  for (const slot of document.querySelectorAll('.ic-slot[data-icon]')) slot.replaceChildren(icon(slot.dataset.icon))
  if (/Mac|iPhone|iPad/.test(navigator.platform || '')) $('search-kbd').textContent = '⌘K'
  drawThemeButton()
  $('theme-btn').addEventListener('click', () => { setTheme(darkNow() ? 'light' : 'dark'); if (ui.route && ui.route.name === 'settings') render() })
  $('help-btn').addEventListener('click', openHelp)
  $('refresh-btn').addEventListener('click', rescan)
  $('search-btn').addEventListener('click', openPalette)
  $('pill').addEventListener('click', togglePop)
  $('scrim').addEventListener('click', (e) => { if (e.target === $('scrim')) closeLayers() })
  document.addEventListener('click', (e) => {
    const pop = $('pop')
    if (!pop.hidden && !pop.contains(e.target) && !$('pill').contains(e.target)) closeLayers()
  })
  document.querySelector('.skip').addEventListener('click', (event) => {
    event.preventDefault()                               // #main is not a route: only move the focus
    $('main').focus()
  })
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', drawThemeButton)
  window.addEventListener('hashchange', navigate)
  window.addEventListener('resize', () => { if (ui.demo) drawTour() })
  document.addEventListener('keydown', onKey)
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll() })
  setInterval(tick, 1000)
  drawSide(parseRoute())
  skeleton()
  setTimeout(poll, calm() ? 0 : 200)
})
