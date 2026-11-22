# Changelog

Notable changes to Orchestraty. The first release is 0.1.0, on PyPI as `orchestraty`.

## Unreleased

### Added

- `orch team`: team sync's first half, for a repository whose remote has a team policy (`.orchestraty/policy.yml`) and people file (`.orchestraty/people.yml`). `orch team init` prints both to start from and `orch team check` reads them; `orch team on` reads the copy in force on the remote's default branch (asking first, since it is a fetch), shows what would be shared, where and when, and asks; a policy that later allows more waits until you agree again. `orch team preview` shows the exact record this machine would send, and what was withheld from it; `orch team task` gives a session a ticket or a task line you confirm; `whoami`, `device` and `off` do the rest. Off by default: a solo user sees nothing new.
- Team sync shares through git. Right after your own successful push to the team remote (git's reflog says when, so no hook is needed and a push git refused sends nothing), the coordinator pushes this device's record to `refs/orchestraty/<you>/<device>`, as a commit in its own shadow repository and with a lease, so it replaces only what this device last saw there; a record with nothing new is not sent. Teammates' records are fetched every two minutes while the coordinator runs (and at its start, and after its own push), with the branches they name, and go through the incoming gate. A failed fetch keeps what was read and waits longer each time, up to an hour. A copied state folder is found when both copies push: `orch team device --new` gives one its own device. While team sync is on somewhere, git's pre-push hook line names the remote and starts the coordinator; run `orch init` again to update the installed hook script.
- With `share.unpushed: true` in the policy, a session whose branch is not on the team remote shares its committed work: the files and Python functions changed from its base to its last commit, read from the commits. What is not committed is never shared. A branch's place on the team remote is where `git push` sends it: a branch made from `origin/main`, which tracks `main`, is not taken as pushed there.
- The overlap rules compare this machine's sessions with teammates' branches, never two teammates' branches with each other: a breaking change on their branch used where you are changing code is strong (read from their commits), a change of yours used where they are changing is told to you, and the same function is medium. A pair where one copy holds the other's commits (your copy holds their tip, or their branch builds on yours) is not compared. Teammates' shared claims take part in O7 and never become claims here.
- Notes about a teammate's branch name them by handle and branch, say the change is committed on that branch and not merged yet, and that nobody on that branch is told from here, so your agent asks you to agree with them; a note about your change used on their branch says their machine tells their sessions once you push it. An overlap with a teammate's claim says when it ends and that it only informs. No note carries a teammate's task line, email, device or session id. `orch status` names a teammate's branch `asha:feat/refunds` in its overlap lines, and merge-order advice `asha's feat/refunds`.
- Notes about teammates arrive as other notes do. Under steer, a breaking change read from a teammate's commits stops the edit it is about, and with wake on it wakes a session waiting for its user. A teammate's shared claim is told before an edit of what it covers, once however often their record is read; it never makes an edit ask or stop, and it is taken back when it leaves their record or its time runs out. git's push line also names the teammates' branches that use what your branch changed.
- `orch status` lists teammates' shared claims under their branch, as informing only, and `--json` gives them in `team.sessions[].team.claims`. The MCP tools answer about teammates in the notes' words: `who_is_changing` lists a teammate's branch and their claims on the file, `check_before_edit` their breaking change and their claims (never hard), and `my_overlaps` names them; a teammate's branch named as `session` is refused. The dashboard names a teammate's branch `asha · feat/refunds`. Where team sync is on, `orch stats` counts teammates' branches, notes by origin and the labels of findings with a teammate's branch, never by person.
- On a host that refuses custom refs (hidden refs, or a pre-receive hook), the record goes to the branch `orchestraty/records` instead, as `records/<person>/<device>.json`, with `[skip ci]` in every commit message; the switch happens on the first refusal and is kept. Two writers pushing there at once both land (each fetches and builds again, up to three times), the branch starts again from one commit past 500, and readers read both stores, the higher seq winning.
- Each branch of the team remote a teammate's record names becomes a session of agent `team`, read from its own commits (never from what the record says of them), one per branch whoever named it. It ends when the branch is merged into the default branch (a merge, a fast forward, or a squash or rebase: merging it would change nothing, or its whole change is a commit there since its base), deleted, no longer named, or stale after `team_stale_days`; it never times out and gets no notes. A branch that also matches a cloud pattern is followed once, as a teammate's.
- `orch team push`, `orch team fetch` and `orch team log`: send this device's record now, read teammates' records now, and list the records this device wrote and what was sent. `orch team off --forget` deletes this device's record ref through the remote's push URL, and `orch uninstall` asks whether to delete it, never anyone else's.
- `orch status` lists teammates' branches under the team block, each with who, its last commit and what its commits changed, and `--json` gains `team.sessions` and `team.fetch`; the dashboard's team card lists them, the MCP `status` tool gives them cleaned, and the Claude Code mod names teammates' and cloud sessions. `orch doctor` says how a push is seen (git's reflog, or the remote-tracking refs where there is none), where records go, a git too old for merge-tree, another machine writing as this device, the last push and fetch, and records sealed under another policy.
- `orch status` and the dashboard show a team block where team sync is on, and `orch status --json --with-lines` adds teammates' confirmed task lines (shown otherwise only on a terminal and in the dashboard); the MCP status tool shows the block cleaned, with teammates' handles but never their devices or labels. `orch doctor` checks team sync where it is on, offline.
- `scripts/real_day.py`: an ordinary day on the built wheel, run in CI and in every release beside the smoke test, on Windows, macOS and Linux.

### Changed

- The record becomes schema 6 when the coordinator opens it. Schema 6 only adds tables, for teammates' work read through git and the task lines you confirm with `orch team task` (only those are written in this version), and every row a 0.1.1 record holds stays as it was. After that, 0.1.x refuses the record with its "newer Orchestraty" message; to go back, move the record aside, with its `-wal` and `-shm` files. `orch events` knows three new kinds for those records: `team_sent`, `team_withheld` and `team_dropped`.
- The coordinator drops a declaration for a cloud agent's branch, as it already dropped a claim for one: nobody works in that branch here, so there is nothing to declare.
- Refresh in `orch ui` shows a striped bar under the top bar while the background process looks at everything again. Its message stays meanwhile, then says what the pass found: how many projects and live sessions, and how many new sessions. When the background process isn't running, the message says so.
- `orch ui`'s buttons have two corners cut on the diagonal, as the arrows in the Orchestraty mark run, instead of rounded corners. Keyboard focus shows as a thicker edge inside the shape.

### Fixed

- After an upgrade the background process went on running the earlier version until someone ran `orch coordinator stop`, so the new version's behaviour (a project without git, say) did not show. It now notices another Orchestraty installed and gives way within a minute (the next agent event starts the new one), and `orch init` restarts an earlier one at once and says so.
- A dashboard left open across an upgrade says so: "Orchestraty X is installed. This dashboard still runs Y", and the same when the background process is still the earlier version. A second `orch ui` beside an earlier dashboard names it and starts the new one on another port.
- A plain-git session whose worktree folder was deleted stayed live, and the coordinator logged an error for it every few seconds; it now ends ("its worktree is gone").

## 0.1.3 (22 November 2026)

One `orch init` for the whole computer: every project your agents work in can be followed from then on, with or without git, and the dashboard has one address and a Projects page.

After upgrading, run `orch coordinator stop` (the next session starts the new version), then `orch init` once: it asks whether to follow every project (or run `orch config track on`). The hook script is unchanged.

### Added

- One `orch init` for the whole computer: run from any folder, it asks once whether to follow every project your agents work in from then on, and offers to set up Codex and Antigravity when they are on the computer. Nothing needs running per project. `orch unwatch` in a project leaves it out; `orch config track on|off` changes the choice later (`watch_all` in `config.json`). A config written before this follows only the projects watched, as before.
- Folder projects: a project folder without git is followed too, through Orchestraty's own snapshots of its files in the state folder (`folders/`), never written into the project and needing no git at all. Its sessions, what each changed, contract changes, notes before an edit, callers left behind at the end of a turn and the same-file and same-folder overlaps all work; the push line, merge order and cloud branches need git. Never the home folder, a drive's top folder or the state folder. `orch status --json` gives each project's `kind` (`git` or `folder`), and a folder project's base as `snap:<id>`.
- `orch ui` at one address, http://127.0.0.1:47392 (a free port when something else holds it). Run again from any folder while it runs, it opens the one already running through a new one-time link, instead of starting a second dashboard.
- A Projects page in `orch ui`: every project, git or not, with its live sessions and open overlaps; a click shows that project, and the switcher at the top follows. Settings has the "Track every project" switch.

### Changed

- A session in a folder without git, which was passed over, is now named as not watched (with a Watch button in `orch ui`) when following every project is off.

## 0.1.2 (21 November 2026)

Fixes from testers' feedback: every agent session shows in `orch status` and `orch ui`, and within about a second (a session started outside a watched repository, sessions in a repository that is not watched, and a coordinator slowed down by many worktrees).

After upgrading, run `orch coordinator stop`: the next session starts the new version, and `orch doctor` says when an older one still runs. The hook script is unchanged, so `orch init` need not run again.

### Fixed

- A session that started outside a watched repository (in a parent folder, say) was passed over for its whole life, even once it worked in a watched one. It now counts from its first step there: a line from another folder, or one naming a file in a watched worktree, gets it another look.
- With many worktrees (the Claude desktop app keeps one per session) a new session could take a minute or more to show in `orch status` and `orch ui`: every few seconds the coordinator looked at every worktree in one pass, idle ones too, before it read any new event. A worktree that stays the same is now looked at less often, down to once a minute, each pass looks at a share of them, and the merge base of a new one is worked out once; a new session shows within about a second.
- `orch ui`'s settings gave `orch together on "<command>"` for the together check, which does not set the command; they now give `orch together set "<command>"`, then `orch together on`.

### Added

- `orch status` and `orch ui` name each repository that is not watched where agent sessions work now, and how many, so their sessions are not silently left out: `orch status` gives the `orch watch` to run, and `orch ui` a Watch button. Only the repository's folder and the count are kept, by the running coordinator (`unwatched.json`); nothing goes into the record. `orch status --json` has them as `unwatched`.
- A Refresh button in `orch ui`'s top bar (and in Ctrl+K): the coordinator looks at every worktree again at once.

## 0.1.1 (19 November 2026)

Fixes from an external audit of 0.1.0, in three parts: the notes are right (findings worked out again after every change that matters, no false or missed notes, nothing lost across a restart), no session's words reach another (what agents declare, the MCP tools' answers, notes and the hook's payload readers hold names only), and install and lifecycle leave no mess (uninstall, settings files that cannot be written, a moved state folder, git hooks, a damaged record, output through a pipe).

After upgrading, run `orch init` again, and `orch init --agent codex` or `--agent antigravity` for those agents: the hook script changed, and `orch doctor` says so until it is replaced. Codex asks you to trust its hooks again in `/hooks` when their lines change.

### Fixed

- `orch uninstall` keeps the state folder, says why and exits 1 when a settings file could not be written or still runs the hook script (a link it leaves alone), any step failed, or the coordinator did not stop. Before, it removed the folder anyway (on Windows only part of it, when the coordinator did not stop) and those hooks then exited 2, which Claude Code treats as blocking. A repository git refuses as another user's (its safe.directory check) is now a step that could not be done, not a traceback, and with `--keep-state` a linked file that still runs the hook script gives exit code 1, not "Nothing of Orchestraty's is left". Run it again once the cause is fixed. It also takes out a push line whose folder is gone, which left git running none of the repository's hooks.
- `orch init`, `orch restore` and `orch uninstall` refuse a read-only settings file, or one another program holds open, with a message (exit code 2 for init and restore) instead of a traceback, and leave no `settings.json.orch-tmp` beside it.
- `orch init` after a change of `ORCH_HOME` (doctor's fix for a state folder in OneDrive) takes out the hooks and the mod the earlier folder's init added, for Claude Code and Codex, and says so first; `orch doctor` flags them, `orch uninstall` takes them out too, and `orch restore --from-backup` leaves them out of the copy it puts back and says so (once the earlier folder is deleted, they would exit 2 on every event). For Codex that includes a folder whose path has an apostrophe, and one whose hook script is gone; for Antigravity, `orch restore --from-backup` leaves an earlier folder's hook out too. The state folder's copy of the mod no longer keeps a read-only attribute from its source, which kept that folder from being deleted. Before deleting the earlier folder, run `orch init` (or `orch unwatch`) in each repository it watched, so the push line moves too.
- `orch init --project` for Claude Code offers the `.git/info/exclude` line SECURITY.md describes (an exclude file it cannot write is said, and init goes on), and `orch uninstall` takes it out again. It takes out every line `orch init --project` added, for Claude Code and Antigravity and in every folder, where it took out only the last one; where nothing else in the file changed it is put back byte for byte, and one `orch init` made is deleted.
- The first `orch init --agent codex` or `--agent antigravity` with a state folder whose path has spaces writes the short-path line that cmd and PowerShell both run (it wrote a PowerShell-only line, and the next init rewrote it). On a volume with no short names the path goes in double quotes. While the state folder is new, `orch init`'s plan shows the long path and says that it is written in its short form. A Codex install whose line changes needs trusting again in its `/hooks`.
- `--python` refuses a program that does not run as Python 3.10 or later (`orch.exe`, say, which made every hook exit 2), and an empty value.
- The push line keeps a `core.hooksPath` written with `~` running as git resolves it, and a relative one (husky's `.husky/_`, `.githooks`) relative, so each worktree runs its own copy of those hooks, as git does (it ran the copy in the worktree where `orch init` ran, and none once that worktree was gone). Taking it out no longer copies a value from your global git config into the repository's: a push line added by an earlier version, which did not record where the value was set, gets back only a relative value or one inside the repository. A `core.hooksPath` set for one worktree only (`git config --worktree`) is refused, since git reads it first and the push line would not run there, and taking the push line out unsets the repository's value whatever one worktree reads.
- Every hook git runs from the repository's earlier hooks folder, ones added after `orch init` included, still runs with the push line. Each costs a shell start, whether the folder has that hook or not: measured on Windows (git 2.53), a `git commit`, which runs four hooks, took about 105 ms more than without the push line (about 170 ms against 65), and a checkout about 25 ms more. `orch doctor` names the few hooks it passes through only once `orch init` runs again (git's hook names only: a README or a helper script in that folder is not one), a push line that runs a hook script that is gone (after a change of `ORCH_HOME`, each push printed an error) and a push line folder that is gone while `core.hooksPath` still names it (no hook ran). `orch init` in a repository that has the push line writes it again, with this state folder's hook script, and rebuilds a folder that is gone (in front of `.git/hooks`, without a traceback); it rewrites only its own scripts, so a hook a hook manager (lefthook) wrote into that folder is left as it is.
- One spool line the record cannot store (half of a surrogate pair, from a file name a model chose) is set aside alone, and the lines read with it are kept; a step the record itself fails, or that runs out of memory, is read again in full. Before, the whole batch of every session's events was lost. The hook script now leaves such a field out and keeps the event (run `orch init` again to update the installed copy).
- A coordinator that cannot open its record (not a database, damaged, a folder, no access) says why once in `coordinator.log`, naming the record, exits 6, and no hook starts another for a minute (it started one every 5 s, silently). One whose record opens but then fails every step (read-only, damaged further in, a full disk) leaves after 20 steps in a row, exits 5, and holds the hooks off the same way. `orch status`, `orch findings` and `orch events` print one line pointing at `orch doctor` instead of a traceback. `orch doctor` checks that the record opens, reads whole and is not read-only, names the record itself when it cannot open it, and says to move a damaged one aside, not to run `orch init`; the coordinator it starts as a test no longer holds the hooks off when it fails, and no longer passes when it fails to open the record just after doctor saw it start.
- `orch doctor` reports a state folder it cannot write to at once on Windows, where it hung, and one where files can be made but not deleted as that; it removes the probe files such a folder kept once it can.
- The together check's command finds no git repository around its folder, even when the state folder is inside one (a home folder kept in git, say), unless the state folder's path has a `;` on Windows or a `:` elsewhere, where git splits `GIT_CEILING_DIRECTORIES`.
- On Windows, `orch status`, `findings`, `events`, `label`, `claim`, `init`, `doctor`, `uninstall` and the other commands no longer end with a UnicodeEncodeError when their output goes to a pipe or a file (as in an agent's shell) and a name, a path or a together check's line has a character outside the ANSI code page. Such output is now UTF-8, like the files orch writes; a terminal keeps its own encoding. Before, `label`, `claim`, `release`, `watch` and `handoff` did their work and then failed, `unwatch` left the push line in the git hooks, and `init` skipped the push-line question. `--json` output stays ASCII.
- `orch ui` reads its own cookie by name: another cookie that a web app on 127.0.0.1 set (a JSON value, a space, a name alone) no longer makes every request a 403.
- The MCP tool `declare` takes only paths of the worktree (or a new one in a folder that is there) and function or class names, and the coordinator drops any other declaration, so an agent's free text, line breaks or terminal escapes no longer reach another agent's notes, `who_is_changing` or `status`. Other sessions' declarations reach the `status` tool and `my_overlaps` cleaned, and `orch status` and `orch events` no longer print control characters. `who_is_changing` for a function says what was done to it, names the right file, does not count the caller as another session and refuses a blank name; `claim`'s refusals read whole.
- The MCP tools' `session` argument names only a session working in the server's own worktree, or in a worktree inside it (where Claude Code puts a desktop worktree session's, whose server it starts in the main checkout): an agent can no longer end another session's claim, claim or declare in its name, or read its task line by naming a session elsewhere. `session` also takes the absolute path of the agent's folder, and with several sessions there the tools ask for it instead of guessing. A server started outside any worktree still answers the questions, without task lines, and refuses `declare`, `claim` and `release`.
- Names in notes keep no `<` or `>` (only a nested function's `<locals>`), so a branch or folder name can no longer open or close a tag in another agent's context.
- A note about two sessions changing one HTTP route (O5) shows a route's path only when it is made of a path's characters and parameters; any other path, which the other session wrote, is left out, and the note says where the route is declared. The MCP tools `status` and `my_overlaps` show it the same way.
- The together check's note names the error and where it happened (`fails with TypeError at tests/test_a.py::test_x`) and no longer quotes the line of output, which the other session's code can write; the MCP tool `status` gives the same. `orch status` still shows you the whole line, now with no control or format characters.
- The MCP tool `status` cleans every name another session controls, as notes do: branches, worktree folders, changed files and functions, claims, findings and merge-order entries, cloud branches; of a TypeScript run's or a cloud fetch's error it keeps only Orchestraty's own words. A session id from an agent's event is only accepted as letters, digits, `-` and `_`. `orch findings` and `orch label` print no control characters.
- The together note names the error and the test only where a test runner names them (`FAILED tests/a.py::test_x - TypeError: ...`, `a.py:5:`), never from words in a test's own message. An O5 note shows Flask's and Django's route parameters in braces (`/users/{int:uid}`), so no note holds a tag-like word. `who_is_changing` answers for any name the record holds again (a non-ASCII one, a nested function's), and `declare` names the new folder to declare for a file in new folders.
- The hook script now reads each field only from the event's own top-level fields: an edit tool's file from the tool's own input, a Codex patch's files from the patch itself, and Antigravity's tool from toolCall's own name. A field of the same name inside a tool's arguments or output can no longer be stored in the spool or the record, or stand in for the session, folder, tool, edited file or Stop flag. Before, an Antigravity argument called `name` could be recorded as the tool, which also missed the edit; text in an apply_patch result that looked like a patch header was recorded as an edited file; and a payload missing a top-level field took it from inside the tool. Reading a payload costs time in proportion to its size, a 5 MB tool result some tens of milliseconds; a payload that starts with a byte order mark is read, and a Codex apply_patch whose input is the patch itself names its files. Run `orch init` again to update the installed hook script (`orch doctor` says when it is another version's).
- Findings are worked out again whenever a session's changed Python files change (and its TypeScript and JavaScript ones, with `orch ts on`), even when no line or signature moves. A call changed in place to a form that breaks now opens its finding and its notes, a call fixed in place resolves it, and the push line, merge order and the together check follow.
- A Python file caught mid-edit with a syntax error, or grown past 2 MB, or not readable for a moment, no longer counts as having all its functions removed, which gave false strong O4 and O6 notes (and could give a false O5). It is not compared until it reads whole again: it stands as the session's copy last showed it, else as its base commit has it, and `orch status` lists it (`changes.not_compared` in `--json`). The record's schema is now 5: an earlier version cannot read it, so going back to 0.1.0 needs the record (`record.sqlite` in the state folder) moved aside first. A coordinator that meets a record a newer Orchestraty wrote now says so in its log, and `orch doctor` says to upgrade or move the record aside.
- Making a plain method a staticmethod or a classmethod, or back, now flags only the calls made on the class (`Cls.m(obj, ...)`, or `cls.m(...)` in a classmethod), not calls on an instance, which still work; a staticmethod made a classmethod, or back, breaks no call. Before, every caller got a false strong note. The engine's calls edges now say whether each call is made on the class (`on_class`), and its cache version is now 5, so each base commit's index is built once again after the upgrade.
- Stopping or restarting the coordinator (`orch coordinator stop`, an upgrade) no longer forgets the files each live session had changed, so open findings stay open and waiting notes are not taken back; `orch status`, merge order and handoff stay right. A session back after it timed out or ended keeps its changes too, and a session that moves to another worktree takes none of its old folder's files with it.
- Callers left behind (O6), the end-of-turn note and the push line keep a caller that a session changed but left on the old form: a caller counts as updated only when its calls, in the copy of the session that changed it, no longer break, judged by what a call written for the new form could not do (pass a name that is gone, leave out a new parameter, pass more arguments by position than the new signature takes), and a call left to a removed function always breaks. Where a call's arguments cannot tell the old form from the new (a reorder, a change of binding, a function made async), and while that copy is still being indexed, a change of the caller still counts. Before, any change of the calling function counted, even one in a worktree without hooks.
- When the notes for one hook came to more than 9,000 characters, the text was cut mid-note and every note was counted as delivered, so the cut ones never reached the agent. A hook now hands over whole notes only, says how many more wait, and the rest come with the next one; a hard claim's note, or with steer on a strong finding's, leads, so an edit that waits or stops says why.

## 0.1.0 (9 November 2026)

Phase 1, on top of the coordination core already in place (the hook script and its installer, the background coordinator and its record, the live map, the overlap rules O1 to O4 and O6, notes written in advance, the push line, steer and wake, `orch status` and the Claude Code mod).

### Added

**Agents and integration levels**

- Codex and Antigravity adapters: `orch init --agent codex|antigravity` and `orch restore --agent`; notes in each agent's own form, only at the events where it can take them; `orch status` says what each session's agent cannot be told. Built; not yet tested with the real agents.
- `orch mcp`, an MCP server over stdio that answers from the record (`who_is_changing`, `check_before_edit`, `my_overlaps`, `status`, `declare`, `claim`, `release`), and `orch mcp --config claude|codex|antigravity`, which prints how to register it.
- The two-call helper for agents you build yourself, in Python (`orchestraty.helper`) and TypeScript (`helpers/typescript`), and an example agent in `examples/homemade-agent`.
- Cloud agents' branches, opt-in with `orch remote on`: fetched read-only into a shadow repository and compared with your sessions as plain-git sessions; `orch status` shows them in a group of their own.
- A Claude Code plugin (`plugins/claude-code`) with the hooks, the mod, the MCP server and a skill, listed in the repository's own marketplace; `orch init`, `orch restore` and `orch doctor` know about it. Built; not yet installed in a real Claude Code session.
- A VS Code and Cursor extension (`editors/vscode`) that shows the sessions and overlaps of the workspace's repository in a status bar item and a panel. Built and unit-tested; not yet tried in the editors.

**Findings and notes**

- Rule W1: two sessions working in the same folder.
- Rule O5: two sessions changing the same HTTP route (FastAPI, Flask and Django routes, read from the syntax tree by the engine).
- Rule O7: the work two sessions declared or claimed overlaps.
- Claims: `orch claim`, `orch claims` and `orch release`, which reach other sessions before they edit what is claimed; a hard claim makes the edit wait for its user's answer, where the agent can ask.
- TypeScript contracts, opt-in for each repository with `orch ts on`: the exported API from the compiler's declarations, or from the source when the project does not compile; their notes say they are heuristic.
- The together check, opt-in: two overlapping sessions' work merged in a scratch copy and run with your own test command.

**Handoffs and merging**

- `orch handoff` packages a session's work; `orch resume` starts an agent on it in a new worktree.
- Merge-order advice, in `orch status` and `orch merge-order`.

**Seeing it**

- `orch ui`, a dashboard in your browser for trying Orchestraty: served on 127.0.0.1 only, through a link that works once, read-only toward the record. `orch ui --demo` plays the whole loop with two scripted sessions in a temporary sandbox.
- From `orch ui` you can label a finding real, harmless or false, claim a file, folder or function for a session and release a claim (each written to orch's spool, as the commands write them), and run `orch doctor`'s checks. In `orch ui --demo` these are recorded at once too.

**Measuring and feedback**

- `orch findings`, `orch label` (real, harmless or false) and `orch missed`.
- `orch stats`: usage week by week, with an acted-on proxy, notes per session per day and what was held back; `--summary` gives counts only.
- `orch feedback`: the stats summary and your own comment, shown first and sent nowhere.
- `orch events`: the record's changes as a stream.

**Installing and removing**

- One distribution, `orchestraty`: the `orch` command and the engine in one pure-Python wheel with no dependencies.
- `orch doctor` checks what Orchestraty needs, with a fix for each problem.
- `orch uninstall` takes out everything Orchestraty added, puts back the settings files `orch init` backed up where nothing is lost, and asks before removing its state folder.

**The record**

- Schema 2 keeps labels and missed-note reports; schema 3 logs every change and keeps declarations; schema 4 keeps claims. An older record is brought up to date when it is opened.

**Repository**

- CI builds the sdist and the wheel and smoke-tests the wheel on Windows and Linux (and macOS in the weekly run); it tests the TypeScript helper, the TypeScript contracts and the editor extension with Node.
- A release workflow that builds, smoke-tests and drafts a GitHub release. Publishing to PyPI is off until the owner turns it on.
- `scripts/first_note_timing.py` times install to first note on a large repository.
- The rules page (`docs/rules.md`), the agents' test results (`docs/agents.json`) and the README table generated from them, a guide for trying Orchestraty (`orchestraty/docs/beta.md`), `SECURITY.md` and `CONTRIBUTING.md`.

### Changed

- `orch ui` is redesigned around what needs you: a home page with the strong overlaps first, overlaps and activity in plain words with the note each agent read, sessions, claims, merge order, settings grouped with an explanation each, a search-and-run palette (Ctrl+K), neutral light and dark themes, and the demo as a side tour that leaves the page visible.
- `orch ui` follows the Orchestraty design system: the Paper and Wine themes, Doppio One in one weight (shipped with the page, under the SIL Open Font License), the Inbound logo, commands as pills with a Copy button that says Copied, and machine output on a terminal ground. One primary button a screen.
- Orchestraty is licensed under the Apache License 2.0.
- Steer stops an edit only for a strong finding's note; every other note only informs, in steer mode too.
- A note still waiting for its agent follows its finding: when another session updates a caller, the note about callers left behind names only the ones still left.
- TypeScript contracts count a use inside a template literal's `${...}`, such as `` `${price(amount)}` ``.
- `orch doctor` says when a repository has more files than the engine reads (20,000 of a commit, in path order), and from which file on they are left out.
- When two sessions change the function that handles an HTTP route they both changed, the agents hear once, through the route's finding, not twice.
- `orch claim` run right after a session starts waits up to 3 seconds for the coordinator to record it, instead of saying no live session works there.
- `orch doctor` says when the running coordinator is another version, one that started before an update, and how to restart it.
