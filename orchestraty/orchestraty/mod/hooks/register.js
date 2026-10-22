// Orchestraty's mod for Claude Code. It shows and hands over; it never detects.
//
// The coordinator, outside Claude Code, writes <state>/status.json whenever something changes.
// Every few seconds this mod reads it and draws it:
//   - a status line: how many other sessions work on this repository, and the overlaps that
//     concern this one;
//   - a band above the prompt, only for strong overlaps that concern this session;
//   - a pane with every session on the repository and every overlap (/orch);
//   - /orch status, /orch steer on|off, /orch wake on|off.
// Notes reach Claude through Orchestraty's settings hooks. The one thing the mod delivers
// itself is a wake note for this session while it waits for its user: it asks Orchestraty's
// hook script to take the note (exactly once, as the hooks do) and submits it as a prompt.
//
// What it touches: Orchestraty's state folder (read), Orchestraty's own Python (started for
// /orch steer, /orch wake and to take a wake note). No network requests.

const PANE = 'orchestraty'
const POLL_MS = 3000
const MAX_BAND_LINES = 3

let me = null              // this session's id
let folder = null          // Orchestraty's state folder
let status = null          // the last status.json read
let shownLine = null       // the status line last shown
let bandKey = ''           // the strong overlaps last drawn in the band
const dismissed = new Set()

async function stateFolder($) {
  const override = await $.env.get('ORCH_HOME')
  if (override) return override
  if ((await $.env.get('OS')) === 'Windows_NT') return (await $.env.get('USERPROFILE')) + '\\.orchestraty'
  const xdg = await $.env.get('XDG_STATE_HOME')
  if (xdg && xdg.startsWith('/')) return xdg + '/orchestraty'
  return (await $.env.get('HOME')) + '/.local/state/orchestraty'
}

function join(base, ...parts) {
  return [base, ...parts].join(base.includes('\\') ? '\\' : '/')
}

function baseName(path) {
  return String(path || '').split(/[\\/]/).filter(Boolean).pop() || String(path || '')
}

// The session id as Orchestraty names its folders: letters, digits, "-" and "_" only.
function safeId(id) {
  return (String(id).match(/[A-Za-z0-9_-]/g) || []).join('').slice(0, 64) || 'nosession'
}

async function readStatus($) {
  try {
    const data = JSON.parse(await $.fs.read(join(folder, 'status.json')))
    return data && data.schema === 1 ? data : null
  } catch {
    return null
  }
}

function myRepository(data) {
  for (const repo of (data && data.repositories) || []) {
    if ((repo.sessions || []).some((s) => s.id === me)) return repo
  }
  return null
}

function concernsMe(o) {
  return o.receiver === me || o.changer === me || (o.sessions || []).includes(me)
}

function strongForMe(repo) {
  return ((repo && repo.overlaps) || []).filter((o) => o.strength === 'strong' && concernsMe(o) && !dismissed.has(o.key))
}

function who(repo, sid) {
  const s = ((repo && repo.sessions) || []).find((x) => x.id === sid)
  if (!s) return String(sid).slice(0, 8)
  return (s.agent === 'git' ? 'plain git' : s.agent) + ' in ' + baseName(s.worktree)
}

// One line for one overlap, as this session sees it.
function describe(repo, o) {
  const name = String(o.symbol || o.file).split('::').pop()
  const file = o.symbol ? String(o.symbol).split('::')[0] : o.file
  if (o.rule === 'O3' || o.rule === 'O4') {
    const how = o.rule === 'O4' ? 'removed' : 'changed'
    if (o.receiver === me) return name + ' (' + file + ') was ' + how + ' by ' + who(repo, o.changer) + '; ' + o.file + ' uses it'
    return name + ' (' + file + '), which you ' + how + ', is used in ' + o.file + ' by ' + who(repo, o.receiver)
  }
  if (o.rule === 'O6') {
    const left = ((o.detail && o.detail.left) || []).length
    return name + ' (' + file + '): ' + left + ' caller' + (left === 1 ? '' : 's') + ' left behind'
  }
  if (o.rule === 'W1') {
    const [a, b] = o.sessions || []
    if (a === me || b === me) return who(repo, a === me ? b : a) + ' works in this same folder'
    return who(repo, a) + ' and ' + who(repo, b) + ' work in one folder'
  }
  const other = (o.sessions || []).find((s) => s !== me)
  if (o.rule === 'O2') return name + ' (' + file + ') is also being changed by ' + who(repo, other)
  return o.file + ' is also being changed by ' + who(repo, other)
}

function statusLine(repo) {
  if (!repo) return undefined
  const others = repo.sessions.filter((s) => s.id !== me).length
  const strong = strongForMe(repo).length
  const medium = (repo.overlaps || []).filter((o) => o.strength === 'medium' && o.rule !== 'W1' && concernsMe(o)).length
  const folder = (repo.overlaps || []).some((o) => o.rule === 'W1' && concernsMe(o))
  if (!others && !strong && !medium && !folder) return undefined
  const parts = [others + ' other session' + (others === 1 ? '' : 's')]
  if (strong) parts.push(strong + ' strong overlap' + (strong === 1 ? '' : 's'))
  if (medium) parts.push(medium + ' shared function' + (medium === 1 ? '' : 's'))
  if (folder) parts.push('sharing this folder')
  return parts.join(' · ')
}

// Orchestraty's interpreter and import path, as orch init wrote them for the hook script.
async function launch($) {
  try {
    const [python, path] = (await $.fs.read(join(folder, 'hooks', 'launch.txt'))).split(/\r?\n/)
    return python ? { python, path: path || '' } : null
  } catch {
    return null
  }
}

async function runOrch($, args) {
  const where = await launch($)
  if (!where) return 'Orchestraty is not installed here: run orch init.'
  const sep = where.python.includes('\\') ? ';' : ':'
  const code = 'import os, sys; os.environ["ORCH_HOME"] = ' + JSON.stringify(folder) +
               '; sys.path[:0] = ' + JSON.stringify(where.path.split(sep).filter(Boolean)) +
               '; from orchestraty.cli import main; sys.exit(main(' + JSON.stringify(args) + '))'
  try {
    const r = await $.process.run([where.python, '-c', code], { timeoutMs: 60000 })
    return (r.stdout + r.stderr).trim() || 'done'
  } catch (err) {
    return 'orch did not run: ' + String((err && err.message) || err)
  }
}

// A wake note waiting for this session: the hook script takes it exactly once.
async function takeWake($) {
  const slot = join(folder, 'notes', safeId(me), 'wake.txt')
  try {
    if (!(await $.fs.exists(slot))) return
    const where = await launch($)
    if (!where) return
    const r = await $.process.run([where.python, '-S', '-I', join(folder, 'hooks', 'hook.py'), 'claude', 'Take', 'wake', me])
    const text = (r.stdout || '').trim()
    if (text) $.prompt.submit({ text })          // runs when the session is idle; not awaited
  } catch {
    // the next tick tries again
  }
}

async function tick($) {
  status = await readStatus($)
  const repo = myRepository(status)
  const line = statusLine(repo)
  if (line !== shownLine) {
    shownLine = line
    $.ui.status(line)
  }
  const key = strongForMe(repo).map((o) => o.key).join('|')
  if (key !== bandKey) {
    bandKey = key
    $.ui.invalidate('ui.render')
  }
  await takeWake($)
}

function summary(repo) {
  if (!repo) return 'Orchestraty does not know this session yet (is its repository watched? run orch watch).'
  const lines = [repo.name + ': ' + repo.sessions.length + ' live session' + (repo.sessions.length === 1 ? '' : 's')]
  for (const s of repo.sessions) {
    const files = Object.keys((s.changes && s.changes.files) || {}).length
    lines.push('  ' + (s.id === me ? 'you' : s.id.slice(0, 8)) + '  ' + s.agent + '  ' + baseName(s.worktree) + '  ' +
               (s.branch || '(detached)') + '  ' + (s.turn || s.state) + '  ' + files + ' file' + (files === 1 ? '' : 's') + ' changed')
  }
  const overlaps = repo.overlaps || []
  lines.push(overlaps.length ? 'Overlaps:' : 'No overlaps.')
  for (const o of overlaps) lines.push('  ' + o.strength + '  ' + o.rule + '  ' + describe(repo, o))
  return lines.join('\n')
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    try {
      me = await $.session.id()
      folder = await stateFolder($)
      $.clock.every(POLL_MS, () => tick($))
      await $.command.register({ name: 'orch', description: 'Orchestraty: sessions on this repository and their overlaps',
                                 argumentHint: '[status | steer on|off | wake on|off]', immediate: true })
    } catch {
      // a taken name or a missing API: the rest of the session goes on
    }
    return next(e)
  })

  on('command.run', { command: 'orch' }, async ($, e) => {
    const args = String(e.args || '').trim().split(/\s+/).filter(Boolean)
    if (!folder) folder = await stateFolder($)
    if (!args.length) {
      status = await readStatus($)
      await $.ui.open({ id: PANE, title: 'Orchestraty', closeOnEscape: true })
      return {}
    }
    if (args[0] === 'status') {
      status = await readStatus($)
      return { text: summary(myRepository(status)) }
    }
    if ((args[0] === 'steer' || args[0] === 'wake') && (args.length === 1 || ['on', 'off'].includes(args[1]))) {
      return { text: await runOrch($, ['config'].concat(args)) }
    }
    return { text: 'Usage: /orch [status | steer on|off | wake on|off]' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const repo = myRepository(status)
    const strong = strongForMe(repo)
    if (!strong.length) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const lines = strong.slice(0, MAX_BAND_LINES).map((o, i) => Text({ key: 'line-' + i, children: [describe(repo, o)] }))
    if (strong.length > MAX_BAND_LINES) lines.push(Text({ key: 'more', dimColor: true, children: ['and ' + (strong.length - MAX_BAND_LINES) + ' more'] }))
    return Box({
      flexDirection: 'column',
      children: [
        Text({ key: 'title', bold: true, children: ['Orchestraty: ' + strong.length + ' strong overlap' + (strong.length === 1 ? '' : 's')] }),
        ...lines,
        Box({
          flexDirection: 'row',
          columnGap: 2,
          children: [
            Button({ key: 'show', label: 'Show all', onPress: async () => { await $.ui.open({ id: PANE, title: 'Orchestraty', closeOnEscape: true }) } }),
            Button({ key: 'dismiss', label: 'Dismiss', onPress: () => { for (const o of strong) dismissed.add(o.key); bandKey = ''; $.ui.invalidate('ui.render') } }),
          ],
        }),
      ],
    })
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const repo = myRepository(status)
    return Box({
      flexDirection: 'column',
      children: summary(repo).split('\n').map((line, i) => Text({ key: 'l' + i, children: [line] })),
    })
  })
}
