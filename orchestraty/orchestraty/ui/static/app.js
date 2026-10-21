// orch ui: draws what the local server reports about the state folder. It decides nothing.
// Everything reaches the page through textContent and DOM calls, never innerHTML: names and
// paths come from the user's code. It talks only to its own server.
'use strict'

const POLL_MS = 2000
const THEME_KEY = 'orch-ui-theme'
const ALL_KEY = 'orch-ui-all'
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
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v)
    else el.setAttribute(k, v === true ? '' : String(v))
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue
    el.append(kid instanceof Node ? kid : String(kid))
  }
  return el
}
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
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return (Math.round(ms * 10) / 10) + ' ms'
  if (ms < 60000) return (Math.round(ms / 100) / 10) + ' s'
  return Math.round(ms / 60000) + ' min'
}
function tick () {
  const now = Date.now()
  for (const el of document.querySelectorAll('time[data-since]')) el.textContent = ago(now - Date.parse(el.dataset.since))
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
  route: null, status: null, detail: null, health: null, push: null, demo: null,
  showAll: stored(ALL_KEY, '0') === '1', lastOk: null, lost: false, denied: false, dirty: true,
  switchResult: null, demoBusy: false, demoError: null
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
  if (route.name === 'overview') want.push = '/api/push'
  if (route.name === 'session') want.detail = '/api/sessions/' + encodeURIComponent(route.id)
  if (route.name === 'overlap') want.detail = '/api/findings/' + encodeURIComponent(route.key)
  if (route.name === 'notes') want.detail = '/api/notes?' + notesQuery(route.params)
  if (route.name === 'settings') { want.detail = '/api/config'; want.health = '/api/health' }
  return want
}

let timer = null
let generation = 0

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
    ui.detail = ui.health = ui.push = null
    ui.demo = null
    for (const [k, r] of got) ui[k] = r.data
    ui.lastOk = new Date()
    ui.lost = false
    ui.dirty = false
    if (changed) render()
  } catch (err) {
    if (err instanceof AccessError) return denied()
    ui.lost = true
  }
  drawHeader()
  if (gen === generation) timer = setTimeout(poll, document.hidden ? POLL_MS * 5 : POLL_MS)
}

function refresh () {
  ui.dirty = true
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
  const route = ui.route
  const views = { overview, session: sessionView, overlap: overlapView, notes: notesView, settings: settingsView }
  swap(document.getElementById('main'), views[route.name]())
  for (const a of document.querySelectorAll('.tabs a')) {
    const current = a.dataset.route === route.name || (a.dataset.route === 'overview' && ['session', 'overlap'].includes(route.name))
    if (current) a.setAttribute('aria-current', 'page')
    else a.removeAttribute('aria-current')
  }
  drawDemo()
  tick()
}

function drawHeader () {
  const st = ui.status
  const coord = document.getElementById('coord')
  const text = document.getElementById('coord-text')
  const c = st && st.coordinator
  if (c && c.running) {
    coord.classList.remove('down')
    text.textContent = 'Coordinator running since ' + clock(c.since) + ', version ' + (c.version || '?')
  } else if (c) {
    coord.classList.add('down')
    text.textContent = 'Coordinator not running: it starts with the next hook event'
  }
  const updated = document.getElementById('updated')
  updated.classList.toggle('lost', ui.lost)
  if (ui.lost) updated.textContent = 'Cannot reach orch ui' + (ui.lastOk ? ' since ' + clock(ui.lastOk.toISOString()) : '')
  else if (ui.lastOk) updated.textContent = 'Updated ' + clock(ui.lastOk.toISOString())
  document.getElementById('demo-badge').hidden = !(st && st.ui && st.ui.demo)
}

function denied () {
  ui.denied = true
  clearTimeout(timer)
  document.getElementById('demo').hidden = true
  swap(document.getElementById('main'), [h('div', { class: 'notice', role: 'alert' },
    h('strong', {}, 'This page can no longer reach orch ui. '),
    'Open the link that orch ui printed in your terminal: it changes each time orch ui starts.')])
  const updated = document.getElementById('updated')
  updated.classList.add('lost')
  updated.textContent = 'No access'
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

function turnChips (s) {
  if (s.state !== 'live') return [chip(s.note || 'ended', 'quiet')]
  if (s.agent === 'git') return [chip('live', 'live'), chip('plain git', 'quiet')]
  return [chip('live', 'live'), chip(TURNS[s.turn] || 'idle', s.turn === 'working' ? '' : 'quiet')]
}

function shortBase (s) {
  return (s.base_commit ? s.base_commit.slice(0, 9) : '—') + (s.base_guessed ? '?' : '')
}

function copyButton (text) {
  const btn = h('button', { type: 'button', class: 'btn small', 'data-key': 'copy:' + text }, 'Copy')
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text)
      btn.textContent = 'Copied'
    } catch {
      const target = btn.previousElementSibling
      if (target) window.getSelection().selectAllChildren(target)
      btn.textContent = 'Selected: press Ctrl+C'
    }
  })
  return btn
}
function command (text) {
  return h('div', { class: 'cmd' }, code(text), copyButton(text))
}

function emptyState (title, body, big) {
  const kids = []
  if (big) {
    const ns = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(ns, 'svg')
    svg.setAttribute('viewBox', '0 0 96 96')
    svg.setAttribute('class', 'mark-big')
    svg.setAttribute('aria-hidden', 'true')
    const path = document.createElementNS(ns, 'path')
    path.setAttribute('d', 'M19 18 L50.2 36 M19 78 L50.2 60')
    path.setAttribute('fill', 'none')
    path.setAttribute('stroke-width', '10')
    path.setAttribute('stroke-linecap', 'round')
    const dot = document.createElementNS(ns, 'circle')
    dot.setAttribute('cx', '71')
    dot.setAttribute('cy', '48')
    dot.setAttribute('r', '11')
    svg.append(path, dot)
    kids.push(svg)
  }
  kids.push(h('h3', {}, title), h('p', {}, body))
  return h('div', { class: 'empty' + (big ? ' big' : '') }, kids)
}

function notFound (what) {
  return [h('a', { class: 'back', href: '#/' }, '← Overview'),
    emptyState(what + ' not found', 'The record has nothing under this name. It may come from an earlier state folder.')]
}

function overlapWhat (o) {
  const name = code(symName(o.symbol))
  const where = code(symFile(o.symbol))
  const d = o.detail || {}
  if (o.rule === 'O1') return [code(o.file)]
  if (o.rule === 'O2') return [name, ' in ', where]
  if (o.rule === 'O3') return [name, ' in ', where, ', used in ', code(o.file)]
  if (o.rule === 'O4') return [name, ' in ', where, ', removed and still used in ', code(o.file)]
  const left = (d.left || []).length
  return [name, ' in ', where, ': ', plural(left, 'caller', 'callers'), ' left behind']
}

function flow (sessions, o) {
  const who = (id) => h('span', { class: 'who' }, whoText(sessions, id))
  const arrow = (text) => h('span', { class: 'arrow', 'aria-hidden': 'true' }, text)
  if (o.rule === 'O3' || o.rule === 'O4') return h('p', { class: 'flow' }, who(o.changer), arrow('→'), who(o.receiver))
  if (o.rule === 'O6') return h('p', { class: 'flow' }, who(o.changer), arrow('→'), h('span', {}, 'callers no session updated'))
  const [a, b] = o.sessions || []
  return h('p', { class: 'flow' }, who(a), arrow('↔'), who(b))
}

function overlapItem (sessions, o) {
  const latent = o.detail && o.detail.latent
  return h('li', { class: 'overlap ' + o.strength },
    h('a', { class: 'stretch', href: overlapHref(o.key) }, RULES[o.rule] || o.rule),
    h('p', { class: 'what' }, overlapWhat(o)),
    flow(sessions, o),
    h('div', { class: 'side' }, chip(o.strength, o.strength), chip(o.rule, 'quiet mono'),
      latent ? chip('latent', 'warn') : null))
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
    (n.rule === 'O3' || n.rule === 'O4') && n.file ? [', used in ', code(n.file)] : null]
}

function noteCard (n, sessions, to) {
  return h('article', { class: 'note' },
    h('div', { class: 'note-head' },
      chip(n.kind, 'mono'), noteState(n),
      h('span', { class: 'about' }, to ? ['to ', whoLink(sessions, n.session_id), ', '] : null, noteWhen(n), ' · ', noteAbout(n))),
    h('dl', { class: 'times' },
      h('div', {}, h('dt', {}, 'Created'), h('dd', {}, at(n.created, true))),
      h('div', {}, h('dt', {}, 'Written'), h('dd', {}, at(n.written, true))),
      h('div', {}, h('dt', {}, 'Delivered'), h('dd', {}, deliveredText(n))),
      h('div', {}, h('dt', {}, 'Time to awareness'), h('dd', {}, msText(n.time_to_awareness_ms)))),
    h('pre', { class: 'text' }, n.text))
}

// ------------------------------------------------------------------------------- the overview
function overview () {
  const st = ui.status
  const repos = (st && st.repositories) || []
  const out = [h('div', { class: 'page-head' }, h('h1', {}, 'Overview'),
    h('p', { class: 'lede' }, 'The sessions on each watched repository, what each has changed since its base commit, ' +
      'and where their changes touch.'))]
  if (!repos.length) {
    out.push(h('div', { class: 'stack' }, emptyState('No watched repository',
      ['Run ', code('orch init'), ' in your repository: it installs the hooks and watches the repository it runs in.'],
      true), command('orch init')))
    return out
  }
  const toggle = h('input', { type: 'checkbox', id: 'show-all', 'data-key': 'show-all' })
  toggle.checked = ui.showAll
  toggle.addEventListener('change', () => {
    ui.showAll = toggle.checked
    store(ALL_KEY, ui.showAll ? '1' : '0')
    refresh()
  })
  out.push(h('div', { class: 'toolbar' },
    h('label', { class: 'check', for: 'show-all' }, toggle, 'Show ended sessions and latent overlaps')))
  const pushes = ui.push ? ui.push.push : []
  const placed = new Set()
  repos.forEach((repo, i) => {
    const ids = new Set(repo.sessions.map((s) => s.id))
    const mine = pushes.filter((p) => p.sessions.some((id) => ids.has(id)))
    mine.forEach((p) => placed.add(p.name))
    out.push(repoSection(repo, i, mine))
  })
  const others = pushes.filter((p) => !placed.has(p.name))
  if (others.length) out.push(h('section', { class: 'section' }, label('Other push lines'), pushPanel(others, {})))
  return out
}

function repoSection (repo, i, pushes) {
  const sessions = {}
  for (const s of repo.sessions) sessions[s.id] = s
  const live = repo.sessions.filter((s) => s.state === 'live').length
  return h('section', { class: 'repo', 'aria-labelledby': 'repo-' + i },
    h('div', { class: 'repo-head' }, h('h2', { id: 'repo-' + i }, repo.name), h('p', { class: 'path mono' }, repo.common_dir)),
    h('section', { class: 'section' },
      h('div', { class: 'section-head' }, label('Sessions', 'h3'), h('span', { class: 'muted' }, plural(live, 'live session', 'live sessions'))),
      repo.sessions.length
        ? h('ul', { class: 'cards' }, repo.sessions.map(sessionCard))
        : emptyState('No live sessions', 'A session shows here as soon as an agent with the hooks starts in this repository, ' +
          'or a worktree of it changes.')),
    h('section', { class: 'section' },
      h('div', { class: 'section-head' }, label('Overlaps', 'h3'), h('span', { class: 'muted' }, plural(repo.overlaps.length, 'overlap', 'overlaps'))),
      repo.overlaps.length ? overlapGroups(repo, sessions)
        : emptyState('No overlaps', 'What these sessions changed does not touch.')),
    pushes.length ? h('section', { class: 'section' }, label('Push lines', 'h3'), pushPanel(pushes, sessions)) : null)
}

function sessionCard (s) {
  const files = Object.entries(s.changes.files || {})
  const unattributed = s.changes.unattributed || []
  const symbols = s.changes.symbols || []
  const breaking = symbols.filter((c) => c.contract === 'breaking').length
  const review = symbols.filter((c) => c.contract === 'review').length
  const shown = files.slice(0, 8)
  return h('li', { class: 'card' },
    h('div', { class: 'card-top' }, h('span', { class: 'agent' }, agentName(s.agent)), h('span', { class: 'chips' }, turnChips(s))),
    h('a', { class: 'stretch worktree', href: sessionHref(s.id) }, baseName(s.worktree)),
    h('p', { class: 'meta' }, h('span', {}, s.branch || '(detached)'), h('span', { class: 'mono' }, 'base ' + shortBase(s))),
    h('p', { class: 'meta' }, h('span', {}, 'last event ', since(s.last_event))),
    h('ul', { class: 'counts', 'aria-label': 'What it changed' },
      h('li', { class: 'chip' }, plural(files.length, 'file', 'files')),
      h('li', { class: 'chip ' + (breaking ? 'breaking' : 'quiet') }, breaking + ' breaking'),
      h('li', { class: 'chip ' + (review ? 'review' : 'quiet') }, review + ' review'),
      unattributed.length ? h('li', { class: 'chip quiet' }, unattributed.length + ' not attributed') : null),
    files.length
      ? h('ul', { class: 'strip', 'aria-label': 'Files changed' },
        shown.map(([path, st]) => h('li', { title: path }, h('span', { class: 'st' }, st), path)),
        files.length > shown.length ? h('li', { class: 'more' }, '+' + (files.length - shown.length) + ' more') : null)
      : null)
}

function overlapGroups (repo, sessions) {
  const titles = { strong: 'Strong', medium: 'Medium', weak: 'Weak' }
  return h('div', { class: 'panel groups' }, STRENGTHS.map((k) => {
    const list = repo.overlaps.filter((o) => o.strength === k)
    if (!list.length) return null
    return h('section', { class: 'group' },
      h('div', { class: 'group-head' }, h('h4', { class: 'label' }, titles[k]), chip(String(list.length), k)),
      h('ul', { class: 'overlaps' }, list.map((o) => overlapItem(sessions, o))))
  }))
}

function pushPanel (pushes, sessions) {
  return h('div', { class: 'panel' }, h('ul', { class: 'pushes' }, pushes.map((p) => h('li', {},
    h('p', { class: 'meta' }, p.session ? whoLink(Object.assign(sessionsOf(), sessions), p.session) : h('span', {}, 'no session known'),
      p.worktree ? h('span', { class: 'mono' }, p.worktree) : null, h('span', {}, 'written ', since(p.modified))),
    h('pre', { class: 'line-out' }, p.line)))))
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
  const out = [h('a', { class: 'back', href: '#/' }, '← Overview'),
    h('header', { class: 'detail-head' },
      h('p', { class: 'label' }, 'Session · ' + agentName(s.agent)),
      h('h1', {}, baseName(s.worktree) || s.id),
      h('div', { class: 'chips' }, turnChips(s), chip(s.branch || '(detached)', 'mono'), chip('base ' + shortBase(s), 'mono')))]
  out.push(h('section', { class: 'panel' }, label('The session'), h('dl', { class: 'facts' },
    fact('Session id', code(s.id)), fact('Agent', agentName(s.agent)), fact('Repository', s.repository.name || '—'),
    fact('Worktree', code(s.worktree || '—')), fact('Branch', s.branch || '(detached)'),
    fact('Base commit', [code(s.base_commit || '—'), s.base_guessed ? ' (guessed: first seen after it started)' : '']),
    fact('Started', at(s.started)), fact('Last event', [at(s.last_event), ' (', since(s.last_event), ')']),
    fact('State', s.state === 'live' ? (s.agent === 'git' ? 'live, plain git: no hooks' : 'live, ' +
      (s.turn === 'working' ? 'working on a turn' : s.turn === 'idle' ? 'waiting for its user' : 'idle')) : (s.note || 'ended')))))
  out.push(h('div', { class: 'cols' },
    h('section', { class: 'panel' }, label('Files changed (' + (files.length + unattributed.length) + ')'),
      files.length || unattributed.length ? h('div', { class: 'table-wrap' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Path'), h('th', {}, 'Status'), h('th', {}, 'This session’s'))),
        h('tbody', {},
          files.map(([p, st]) => h('tr', {}, h('td', {}, code(p)), h('td', {}, st + ' · ' + (FILE_STATUS[st] || st)), h('td', {}, 'yes'))),
          unattributed.map((p) => h('tr', {}, h('td', {}, code(p)), h('td', {}, '—'),
            h('td', {}, 'not attributed: changed in a folder another live session shares'))))))
        : h('p', { class: 'muted' }, 'Nothing changed since its base commit.')),
    h('section', { class: 'panel' }, label('Functions and classes (' + symbols.length + ')'),
      symbols.length ? h('div', { class: 'table-wrap' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Symbol'), h('th', {}, 'Change'), h('th', {}, 'Contract'), h('th', {}, 'Reasons'))),
        h('tbody', {}, symbols.map((c) => h('tr', {},
          h('td', {}, code(symName(c.symbol)), h('br'), h('span', { class: 'muted' }, symFile(c.symbol)),
            c.moved_from ? [h('br'), h('span', { class: 'muted' }, 'from ', code(c.moved_from))] : null),
          h('td', {}, c.kind, c.change ? ' · ' + c.change : ''),
          h('td', {}, chip(c.contract || 'none', c.contract || 'none')),
          h('td', {}, c.reasons && c.reasons.length ? h('ul', { class: 'reasons' }, c.reasons.map((r) => h('li', {}, r))) : '—'))))))
        : h('p', { class: 'muted' }, 'No Python function or class changed.'))))
  out.push(h('section', { class: 'section' }, label('Notes to this session (' + d.notes_received.length + ')'),
    d.notes_received.length ? h('div', { class: 'notes' }, d.notes_received.map((n) => noteCard(n, sessions, false)))
      : emptyState('No notes', 'Nothing another session did has concerned this one so far.')))
  out.push(h('section', { class: 'section' }, label('Notes its changes caused for other sessions (' + d.notes_caused.length + ')'),
    d.notes_caused.length ? h('div', { class: 'notes' }, d.notes_caused.map((n) => noteCard(n, sessions, true)))
      : emptyState('No notes', 'No other session has been told about this one’s changes.')))
  if (d.push) {
    out.push(h('section', { class: 'panel' }, label('Push line for this worktree'),
      h('pre', { class: 'line-out' }, d.push.line), h('p', { class: 'muted' }, 'git prints it at push. It never blocks a push.')))
  }
  out.push(h('section', { class: 'panel' }, label('Events, latest first (' + d.events.length + ')'),
    d.events.length ? h('ol', { class: 'timeline' }, d.events.map((e) => h('li', {},
      at(e.ts, true), h('span', { class: 'ev' }, e.event),
      h('span', { class: 'what' },
        e.tool_name || '', e.file_path ? [' ', code(relPath(s.worktree, e.file_path))] : '',
        e.took ? (e.tool_name || e.file_path ? ' · ' : '') + 'took its ' + e.took + ' note' : '',
        e.source ? 'source: ' + e.source : '', e.reason ? 'reason: ' + e.reason : ''))))
      : h('p', { class: 'muted' }, 'No events recorded.')))
  return out
}

function fact (name, value) {
  return h('div', {}, h('dt', {}, name), h('dd', {}, value))
}

// ------------------------------------------------------------------------------- an overlap
function overlapView () {
  const d = ui.detail
  if (!d) return notFound('Overlap')
  const f = d.finding
  const sessions = Object.assign(sessionsOf(), d.sessions || {})
  const det = f.detail || {}
  const out = [h('a', { class: 'back', href: '#/' }, '← Overview'),
    h('header', { class: 'detail-head' },
      h('p', { class: 'label' }, 'Overlap · ' + f.rule),
      h('h1', {}, RULES[f.rule] || f.rule),
      h('div', { class: 'chips' }, chip(f.strength, f.strength), chip(f.state, f.state === 'open' ? '' : 'quiet'),
        f.latent ? chip('latent', 'warn') : null))]
  out.push(h('section', { class: 'panel' }, label('Who and what'), picture(f, sessions)))
  out.push(h('section', { class: 'panel' }, label('The finding'), h('dl', { class: 'facts' },
    f.symbol ? fact('Function', [code(symName(f.symbol)), ' in ', code(symFile(f.symbol))]) : null,
    fact('File', code(f.file || '—')),
    det.moved_to ? fact('Moved to', code(det.moved_to)) : null,
    fact('Reasons', det.reasons && det.reasons.length ? h('ul', { class: 'reasons' }, det.reasons.map((r) => h('li', {}, r))) : '—'),
    fact('Latent', f.latent ? 'yes: the receiving session uses it in a file it is not changing yet; it hears before its first edit there'
      : 'no'),
    fact('State', f.state), fact('First seen', at(f.first_seen)), fact('Last seen', at(f.last_seen)),
    f.resolved ? fact('Resolved', at(f.resolved)) : null)))
  const rows = f.rule === 'O6' ? (det.left || []).map((c) => [c.symbol, c.file || symFile(c.symbol), c.lines])
    : (det.uses || []).map((u) => [u.symbol, symFile(u.symbol), u.lines])
  if (f.rule === 'O6' || f.rule === 'O3' || f.rule === 'O4') {
    out.push(h('section', { class: 'panel' },
      label(f.rule === 'O6' ? 'Callers left behind (' + rows.length + ')' : 'Uses (' + rows.length + ')'),
      rows.length ? h('div', { class: 'table-wrap' }, h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, 'Function'), h('th', {}, 'File'), h('th', {}, 'Lines'))),
        h('tbody', {}, rows.map(([sym, file, lines]) => h('tr', {}, h('td', {}, code(symName(sym))), h('td', {}, code(file)),
          h('td', { class: 'num' }, (lines || []).join(', ') || '—'))))))
        : h('p', { class: 'muted' }, 'None listed.')))
  }
  if (f.rule === 'O2' && det.kinds) {
    out.push(h('section', { class: 'panel' }, label('What each session did to it'), h('dl', { class: 'facts' },
      Object.entries(det.kinds).map(([sid, kind]) => fact(whoText(sessions, sid), kind)))))
  }
  out.push(h('section', { class: 'panel' }, label('Notes made for it (' + f.notes.length + ')'),
    f.notes.length ? h('div', { class: 'table-wrap' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, 'To'), h('th', {}, 'Kind'), h('th', {}, 'State'), h('th', {}, 'Written'),
        h('th', {}, 'Delivered'), h('th', {}, 'Time to awareness'), h('th', {}, 'Text'))),
      h('tbody', {}, f.notes.map((n) => h('tr', {},
        h('td', { class: 'nowrap' }, whoLink(sessions, n.session_id)), h('td', { class: 'nowrap' }, chip(n.kind, 'mono')),
        h('td', { class: 'nowrap' }, noteState(n)), h('td', { class: 'num nowrap' }, at(n.written, true)),
        h('td', { class: 'num' }, deliveredText(n)), h('td', { class: 'num nowrap' }, msText(n.time_to_awareness_ms)),
        h('td', { class: 'nowrap' }, h('details', { 'data-key': 'note-' + n.id }, h('summary', { 'data-key': 'show-' + n.id }, 'Show'), h('pre', { class: 'text' }, n.text))))))))
      : h('p', { class: 'muted' }, f.rule === 'O1' ? 'None: the same file in two sessions only shows here.' : 'None made.')))
  return out
}

function picture (f, sessions) {
  const node = (id, role) => h('div', { class: 'node' }, h('span', { class: 'label' }, role), whoLink(sessions, id),
    h('span', { class: 'muted' }, agentName((sessions[id] || {}).agent) + ((sessions[id] || {}).branch ? ' · ' + sessions[id].branch : '')))
  const arrow = (text, cls) => h('div', { class: 'link-arrow ' + (cls || '') }, h('span', {}, text), h('span', { class: 'shaft', 'aria-hidden': 'true' }))
  const name = symName(f.symbol)
  if (f.rule === 'O3' || f.rule === 'O4') {
    return h('div', { class: 'picture' }, node(f.changer, 'Changing session'),
      arrow((f.rule === 'O4' ? 'removed ' : 'breaking change to ') + name, f.strength),
      node(f.receiver, 'Receiving session'))
  }
  if (f.rule === 'O6') {
    const left = (f.detail.left || []).length
    return h('div', { class: 'picture' }, node(f.changer, 'Changing session'), arrow('breaking change to ' + name, f.strength),
      h('div', { class: 'node none' }, h('span', { class: 'label' }, 'Left behind'),
        h('strong', {}, plural(left, 'caller', 'callers')), h('span', { class: 'muted' }, 'that no session has updated')))
  }
  const [a, b] = f.sessions
  return h('div', { class: 'picture' }, node(a, 'Session'), arrow('both changed ' + (name || f.file), f.strength + ' both'), node(b, 'Session'))
}

// ------------------------------------------------------------------------------- the notes
function notesView () {
  const d = ui.detail
  const params = ui.route.params
  const out = [h('div', { class: 'page-head' }, h('h1', {}, 'Notes'),
    h('p', { class: 'lede' }, 'Every note made for an agent, newest first. Time to awareness runs from the event that caused ' +
      'a note to the note being ready in its slot for the agent.'))]
  if (!d) return out
  const select = (name, title, options) => {
    const el = h('select', { id: 'filter-' + name, 'data-key': 'filter-' + name },
      h('option', { value: '' }, 'All'), options.map(([value, text]) => h('option', { value }, text)))
    el.value = params.get(name) || ''
    el.addEventListener('change', () => {
      const next = new URLSearchParams(notesQuery(params))
      if (el.value) next.set(name, el.value)
      else next.delete(name)
      const q = next.toString()
      location.hash = '#/notes' + (q ? '?' + q : '')
    })
    return h('label', { for: 'filter-' + name }, title, el)
  }
  const sessions = Object.assign(sessionsOf(), d.sessions || {})
  const sessionOptions = Object.keys(d.sessions || {}).map((id) => [id, whoText(sessions, id) + ' · ' + id.slice(0, 8)])
  out.push(h('div', { class: 'filters', role: 'search', 'aria-label': 'Filter the notes' },
    select('session', 'Session', sessionOptions), select('kind', 'Kind', d.kinds.map((k) => [k, k])),
    select('state', 'State', d.states.map((s) => [s, s]))))
  const m = d.summary
  out.push(h('div', { class: 'tiles' },
    tile(String(d.total), d.total === 1 ? 'note' : 'notes', 'Notes'),
    tile(String(m.delivered), 'delivered to their agent', 'Delivered'),
    tile(msText(m.median_ms), m.measured ? 'median over ' + plural(m.measured, 'delivered note', 'delivered notes') : 'no delivered note yet',
      'Median time to awareness')))
  if (!d.notes.length) {
    out.push(emptyState('No notes', 'No note matches. Notes appear when one session’s change concerns another.'))
    return out
  }
  out.push(h('section', { class: 'panel' }, h('div', { class: 'table-wrap' }, h('table', {},
    h('caption', { class: 'sr-only' }, 'Notes'),
    h('thead', {}, h('tr', {}, ['Created', 'To', 'Kind', 'State', 'About', 'Delivered', 'Time to awareness', 'Text']
      .map((t) => h('th', {}, t)))),
    h('tbody', {}, d.notes.map((n) => h('tr', {},
      h('td', { class: 'num nowrap' }, at(n.created, true)), h('td', { class: 'nowrap' }, whoLink(sessions, n.session_id)),
      h('td', { class: 'nowrap' }, chip(n.kind, 'mono')), h('td', { class: 'nowrap' }, noteState(n)),
      h('td', {}, noteAbout(n)), h('td', { class: 'num' }, deliveredText(n)),
      h('td', { class: 'num nowrap' }, msText(n.time_to_awareness_ms)),
      h('td', { class: 'nowrap' }, h('details', { 'data-key': 'note-' + n.id }, h('summary', { 'data-key': 'show-' + n.id }, 'Show'), h('pre', { class: 'text' }, n.text))))))))))
  if (d.total > d.notes.length) out.push(h('p', { class: 'muted' }, 'The newest ' + d.notes.length + ' of ' + d.total + ' are listed.'))
  return out
}

function tile (big, small, name) {
  return h('div', { class: 'panel tile' }, h('p', { class: 'label' }, name), h('p', { class: 'big' }, big), h('p', { class: 'muted' }, small))
}

// ------------------------------------------------------------------------------- settings
function settingsView () {
  const cfg = ui.detail
  const health = ui.health
  const out = [h('div', { class: 'page-head' }, h('h1', {}, 'Settings'),
    h('p', { class: 'lede' }, 'The two switches go through orch config. Everything else is set from the command line.'))]
  if (!cfg) return out
  const sw = (name, title, text) => {
    const on = !!cfg[name]
    const btn = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(on), 'data-key': 'switch-' + name,
      'aria-describedby': 'about-' + name }, h('span', { class: 'knob', 'aria-hidden': 'true' }), on ? 'On' : 'Off')
    btn.addEventListener('click', async () => {
      btn.disabled = true
      try {
        const res = await post('/api/config', { [name]: on ? 'off' : 'on' })
        ui.switchResult = { lines: res.lines || [], warn: !!res.error, error: res.error }
      } catch (err) {
        if (err instanceof AccessError) return denied()
        ui.switchResult = { lines: [String(err.message || err)], warn: true }
      }
      refresh()
    })
    return h('div', { class: 'switch-row' }, h('div', {}, h('h3', { id: 'title-' + name }, title), h('p', { id: 'about-' + name }, text)),
      h('div', { 'aria-labelledby': 'title-' + name }, btn))
  }
  const result = ui.switchResult
  out.push(h('section', { class: 'panel' }, label('Switches'), h('div', { class: 'switches' },
    sw('steer', 'Steer', 'A note before an edit stops that edit with the reason, so the agent re-plans. Off: the note only informs, ' +
      'and the agent’s usual permission prompts apply.'),
    sw('wake', 'Wake', 'A strong note for a session that waits for its user wakes it. Turning it on or off also updates ' +
      'the hooks where orch init installed them.'),
    h('p', { class: 'result' + (result && result.warn ? ' warn' : ''), role: 'status' },
      result ? result.lines.concat(result.error ? [result.error] : []).join('\n') : ''))))
  out.push(h('section', { class: 'panel' }, label('Watched repositories (' + cfg.repos.length + ')'),
    cfg.repos.length ? h('ul', { class: 'repos' }, cfg.repos.map((r) => h('li', {},
      h('h3', {}, r.name), h('p', { class: 'muted mono' }, r.common_dir), command(r.watch), command(r.unwatch))))
      : h('div', { class: 'stack' }, h('p', { class: 'muted' }, 'None. Watch a repository from inside it:'), command('orch watch'))))
  const c = health && health.coordinator
  out.push(h('section', { class: 'panel' }, label('The state folder'), h('div', { class: 'stack' },
    command(cfg.state),
    h('dl', { class: 'facts' },
      fact('Coordinator', c ? (c.running ? 'running since ' + clock(c.since) + ', version ' + (c.version || '?') + ', pid ' + (c.pid || '?')
        : 'not running: it starts with the next hook event') : '—'),
      fact('Record', health ? (health.record.exists ? code(health.record.path) : 'not made yet: the coordinator makes it') : '—'),
      fact('Hooks installed in', cfg.settings ? code(cfg.settings)
        : ui.status && ui.status.ui && ui.status.ui.demo ? 'nowhere: the demo writes no Claude Code settings'
          : 'nowhere yet: run orch init')))))
  out.push(h('section', { class: 'panel' }, label('Install and remove'), h('div', { class: 'stack' },
    h('p', {}, 'Install the hooks, the push line and the Claude Code mod, and watch the repository you run it in:'),
    command(cfg.commands.init),
    h('p', {}, 'Take Orchestraty’s hook entries out of the Claude Code settings, leaving everything else:'),
    command(cfg.commands.restore))))
  return out
}

// ------------------------------------------------------------------------------- the sandbox demo
function drawDemo () {
  const box = document.getElementById('demo')
  const d = ui.demo
  if (!d) {
    box.hidden = true
    return
  }
  box.hidden = false
  const done = d.steps.filter((s) => s.done)
  const last = done[done.length - 1]
  const button = h('button', { type: 'button', class: 'btn primary', id: 'demo-next', 'data-key': 'demo-next',
    disabled: ui.demoBusy || d.next === null }, ui.demoBusy ? 'Running…' : 'Next step')
  button.addEventListener('click', nextStep)
  const count = ui.demoError ? ui.demoError : plural(done.length, 'step', 'steps') + ' of ' + d.steps.length + ' done'
  if (ui.route && ui.route.name !== 'overview') {
    swap(box, [h('div', { class: 'demo-bar' }, h('p', { class: 'label', id: 'demo-title' }, 'Sandbox demo'),
      h('p', { class: 'now', role: 'status' }, count, last ? ': ' + last.text : ''),
      d.next === null ? null : button, h('a', { href: '#/' }, 'Steps and what they printed'))])
    return
  }
  const left = h('div', { class: 'demo-intro' },
    h('p', { class: 'label' }, 'Sandbox demo'),
    h('h2', { id: 'demo-title' }, 'The whole loop, step by step'),
    h('p', {}, 'Two pretend Claude Code sessions, driven through the real hook script, in a temporary folder that is removed when ' +
      'orch ui stops: ', code(d.folder)),
    h('ol', { class: 'steps' }, d.steps.map((s) => h('li', { class: s.done ? 'done' : s.n === d.next ? 'next' : '' },
      h('span', { class: 'n', 'aria-hidden': 'true' }, String(s.n)),
      h('span', {}, h('span', { class: 'sr-only' }, 'Step ' + s.n + (s.done ? ', done: ' : s.n === d.next ? ', next: ' : ': ')), s.text)))),
    h('div', { class: 'demo-actions' },
      d.next === null ? h('p', {}, 'The scenario is done. Stop orch ui with Ctrl+C: the temporary folder goes with it.') : button,
      h('p', { class: 'muted', role: 'status' }, count)))
  const right = h('div', { class: 'demo-out' }, last
    ? h('div', { class: 'panel' }, h('p', { class: 'label' }, 'Step ' + last.n), h('h3', {}, last.text),
      last.outputs.map((o, i) => o.fold
        ? h('details', { 'data-key': 'out-' + last.n + '-' + i }, h('summary', { 'data-key': 'out-sum-' + last.n + '-' + i }, o.label),
          h('pre', { class: 'text' }, o.text))
        : [h('p', { class: 'out-label' }, o.label), h('pre', { class: 'text' }, o.text)]))
    : h('div', { class: 'panel' }, h('p', { class: 'label' }, 'Ready'), h('h3', {}, 'A repository with two worktrees'),
      h('p', { class: 'muted' }, 'a.py defines charge(amount, currency) and b.py calls it. Session A works in wt-a, session B in wt-b. ' +
        'Press Next step to start them.')))
  swap(box, [h('div', { class: 'demo-inner' }, left, right)])
}

async function nextStep () {
  if (ui.demoBusy) return
  ui.demoBusy = true
  ui.demoError = null
  drawDemo()
  try {
    const d = await post('/api/demo/next')
    cache.set('/api/demo', { etag: null, data: d })
    ui.demo = d
  } catch (err) {
    if (err instanceof AccessError) return denied()
    ui.demoError = 'The step did not run: ' + (err.message || err)
  }
  ui.demoBusy = false
  refresh()
}

// ------------------------------------------------------------------------------- start
document.addEventListener('DOMContentLoaded', () => {
  for (const b of document.querySelectorAll('[data-theme-choice]')) {
    b.addEventListener('click', () => {
      store(THEME_KEY, b.dataset.themeChoice)
      applyTheme(b.dataset.themeChoice)
    })
  }
  applyTheme(stored(THEME_KEY, 'system'))
  document.querySelector('.skip').addEventListener('click', (event) => {
    event.preventDefault()                               // #main is not a route: only move the focus
    document.getElementById('main').focus()
  })
  window.addEventListener('hashchange', () => {
    document.getElementById('main').focus({ preventScroll: true })
    window.scrollTo(0, 0)
    refresh()
  })
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll() })
  setInterval(tick, 1000)
  poll()
})
