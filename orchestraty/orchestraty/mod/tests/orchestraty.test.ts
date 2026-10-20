// Run with: claude plugin test   (from this mod's folder; Claude Code 2.1.287 or later)
import { expect, mock, test } from 'claude-code/testing'

const STATE = '/state'
const STATUS = {
  schema: 1,
  coordinator: { running: true },
  repositories: [{
    name: 'repo',
    common_dir: '/w/repo/.git',
    sessions: [
      { id: 'A', agent: 'claude', worktree: '/w/wt-a', branch: 'feat-a', state: 'live', turn: 'working',
        changes: { files: { 'a.py': 'M' }, unattributed: [], symbols: [] } },
      { id: 'B', agent: 'claude', worktree: '/w/wt-b', branch: 'feat-b', state: 'live', turn: 'idle',
        changes: { files: { 'c.py': 'A' }, unattributed: [], symbols: [] } },
    ],
    overlaps: [
      { key: 'O3|a.py::charge|A|B|c.py', rule: 'O3', strength: 'strong', symbol: 'a.py::charge', file: 'c.py',
        changer: 'A', receiver: 'B', sessions: ['A', 'B'], detail: {} },
      { key: 'O6|a.py::charge|A', rule: 'O6', strength: 'strong', symbol: 'a.py::charge', file: 'a.py',
        changer: 'A', receiver: '', sessions: ['A'], detail: { left: [{ symbol: 'b.py::pay' }] } },
    ],
  }],
}
const BAND = {
  plugin: 'orchestraty', component: 'AbovePrompt', requestId: 'above', surface: 'terminal',
  viewport: { columns: 100, rows: 30 },
  props: { hasSurvey: false, isWorking: false, maxRows: 8, bodyColumns: 90, scroll: { offset: 0, bodyRows: 8 }, view: {} },
} as const

// Stubs for everything the mod asks Claude Code for, as session B.
function stubs(on, extra: { wake?: string, said?: string[], prompts?: string[], runs?: string[][] } = {}) {
  const clock = mock.clock(on)
  mock.env(on, { ORCH_HOME: STATE, OS: 'Windows_NT' })
  on('session.start', () => ({ cwd: '/w/wt-b' }))
  on('session.id', () => ({ value: 'B' }))
  on('command.register', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', ($, e) => { extra.said?.push(e.text); return { value: undefined } })
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  on('fs.read', ($, e) => {
    if (e.path.endsWith('status.json')) return { value: JSON.stringify(STATUS) }
    if (e.path.endsWith('launch.txt')) return { value: 'C:\\py\\python.exe\nC:\\src\\orchestraty;C:\\src\\engine\n' }
    return { deny: 'no such file' }
  })
  on('fs.exists', ($, e) => ({ value: Boolean(extra.wake) && e.path.endsWith('wake.txt') }))
  on('process.run', ($, e) => {
    extra.runs?.push(e.argv)
    if (e.argv.includes('Take')) return { value: { exitCode: 0, stdout: extra.wake ?? '', stderr: '' } }
    return { value: { exitCode: 0, stdout: 'steer: on', stderr: '' } }
  })
  on('prompt.submit', ($, e) => { extra.prompts?.push(e.text); return { text: e.text } })
  return clock
}

async function start($) {
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/w/wt-b' })
}

test('/orch status lists the sessions and the overlaps as this session sees them', async ($, on) => {
  stubs(on)
  await start($)
  const answer = await $.command.run({ command: 'orch', args: 'status' })
  expect(answer.text).toContain('repo: 2 live sessions')
  expect(answer.text).toContain('you  claude  wt-b  feat-b  idle  1 file changed')
  expect(answer.text).toContain('strong  O3  charge (a.py) was changed by claude in wt-a; c.py uses it')
})

test('the status line counts the other sessions and the strong overlaps for this one', async ($, on) => {
  const said: string[] = []
  const clock = stubs(on, { said })
  await start($)
  await clock.advance(3000)
  expect(said).toEqual(['1 other session · 1 strong overlap'])
  await clock.advance(3000)
  expect(said.length).toBe(1)                       // unchanged: not shown again
})

test('the band shows a strong overlap for this session and can be dismissed', async ($, on) => {
  const clock = stubs(on)
  await start($)
  await clock.advance(3000)
  const band = await $.ui.mount(BAND)
  expect(await band.find({ type: 'Text', text: 'Orchestraty: 1 strong overlap' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: /charge \(a\.py\) was changed by claude in wt-a/ })).toBeDefined()
  await band.press({ key: 'dismiss' })
  await band.unmount()
  const after = await $.ui.mount(BAND)
  expect(await after.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
})

test('a wake note for this session is taken by the hook script and submitted', async ($, on) => {
  const prompts: string[] = []
  const runs: string[][] = []
  const clock = stubs(on, { wake: 'another session changed charge(); c.py calls it', prompts, runs })
  await start($)
  await clock.advance(3000)
  expect(runs[0].slice(-4)).toEqual(['claude', 'Take', 'wake', 'B'])
  expect(prompts).toEqual(['another session changed charge(); c.py calls it'])
})

test('/orch steer on runs orch config with Orchestraty\'s own Python', async ($, on) => {
  const runs: string[][] = []
  stubs(on, { runs })
  await start($)
  const answer = await $.command.run({ command: 'orch', args: 'steer on' })
  expect(answer.text).toBe('steer: on')
  expect(runs[0][0]).toBe('C:\\py\\python.exe')
  expect(runs[0][2]).toContain('main(["config","steer","on"])')
  const usage = await $.command.run({ command: 'orch', args: 'steer maybe' })
  expect(usage.text).toContain('Usage: /orch')
})
