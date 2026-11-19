"""Session kinds: which sessions are an agent working on this machine, and which are not.

A session is named by its agent. Most are an agent's, here, with hooks. Three kinds are not:

* ``git`` (LEVEL0): a worktree with changes and no hooked session, followed through git alone.
  It has a working copy here, but no agent to tell.
* ``cloud``: a cloud agent's branch, read from a shadow repository. Its agent works elsewhere.
* ``team``: a teammate's branch, read from a shadow repository. Its people work elsewhere.

Every part of Orchestraty asks these groups instead of naming agents, so a new kind is added
here once:

* NO_NOTES: no hooks, so nothing can be delivered; it keeps no coordinator awake.
* NO_COPY: no working copy on this disk, so it is never claimed, declared, checked together,
  handed off or given a push line.
* REMOTE: its work lives elsewhere and is read from a shadow repository: it never times out,
  it is followed from its branch's tip, and it is not listed among the sessions here.
"""
LEVEL0 = "git"
CLOUD = "cloud"
TEAM = "team"
NO_NOTES = (LEVEL0, CLOUD, TEAM)
NO_COPY = (CLOUD, TEAM)
REMOTE = (CLOUD, TEAM)
