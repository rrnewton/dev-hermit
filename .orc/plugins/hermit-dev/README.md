# hermit-dev ORC plugin

Auto-loads the canonical `dev-hermit` coordinator policies into every ORC
session. The policy text is not duplicated here: the plugin reads the
workspace's `AGENTS.md` (the same file `CLAUDE.md` symlinks to) at startup and
registers it as the `hermit-dev` skill, so a single source stays authoritative.

## What this plugin does

- Registers a `hermit-dev` skill whose instructions are the current
  `AGENTS.md` contents (parent-workspace coordination, fork-only issue policy,
  Git/PR workflow, Reverie API policy, and product vision).
- Activates those policies on session startup
  (`hermit-dev.startup`), so agents receive them without a manual step.
- Exposes helper functions:
  - `await orc.hermit-dev.activate()` — reload `AGENTS.md` and re-activate the
    policies (use after editing the policy file).
  - `orc.hermit-dev.status()` — report registration and policy-source state.
- Ships `gh-issue-create`, the wrapper that keeps agent-created GitHub issues
  on the `rrnewton` forks (never `facebookexperimental`).

`AGENTS.md` is resolved across both install layouts: first relative to the
plugin directory (`orc.pluginDir() + "/../../../AGENTS.md"`, which hits the repo
root when the plugin runs from its in-repo source location), then falling back
to `<home>/work/dev-hermit/AGENTS.md` (home derived from `orc.userInfo()`, used
when the plugin runs as the installed home copy). See `resolvePolicy()` in
`index.ts`.

## Install

The plugin source is version-controlled inside this repository. ORC only
discovers "home" plugins under `~/.orc/plugins/`, so install a **real copy**
there and load it from your ORC config.

> **Why a copy and not a symlink?** ORC's module sandbox resolves symlinks and
> rejects imports whose real path escapes the managed module roots. A symlinked
> plugin dir makes the auto-generated `orc_plugin_loader.js` do
> `import "./index.ts"`, which resolves through the symlink to a path outside
> `~/.orc/plugins/` — ORC refuses it with *"Relative import './index.ts'
> escapes managed module roots"*, and the plugin fails to load on every session
> start. A real copy keeps `index.ts` inside the managed root.

```bash
# 1. Install (or refresh) the plugin as a real copy under ~/.orc/plugins/.
~/work/dev-hermit/.orc/plugins/hermit-dev/install.sh

# 2. Auto-load it at every session startup by appending to ~/.orc/config.js
#    (append — do NOT overwrite existing content):
printf '\norc.loadPlugin("hermit-dev");\n' >> ~/.orc/config.js
```

Start a new ORC session (or run `orc.loadPlugin("hermit-dev")` in the current
one) to load it.

### Keeping the copy in sync

Because it is a copy (not a symlink), edits to the source under
`~/work/dev-hermit/.orc/plugins/hermit-dev/` do **not** take effect until you
re-run `install.sh`. Re-run it after editing the plugin, e.g. from a git
`post-merge` / `post-checkout` hook or manually. (Editing the policy text in
`AGENTS.md` itself needs no re-copy — it is read at startup, or via
`await orc.hermit-dev.activate()`.)

## Why a home copy + config.js instead of `registerSkill`

- `orc.registerSkill(...)` is in-memory only; it is not persisted across
  sessions, so the policies would silently disappear on the next start.
- Home plugins (`~/.orc/plugins/`) are always discovered. Project plugins
  (`<repo>/.orc/plugins/`) are only discovered if the project root existed at
  ORC startup, which is unreliable for a workspace opened mid-session.
- `orc.loadPlugin(...)` in `~/.orc/config.js` runs at every session startup, so
  the plugin (and thus the `AGENTS.md` policies) load deterministically.

## Verify

```bash
# It is a real directory, not a symlink:
[ -L ~/.orc/plugins/hermit-dev ] && echo "SYMLINK (bad)" || echo "real dir (good)"
ls ~/.orc/plugins/hermit-dev   # index.ts, package.json, gh-issue-create, README.md, install.sh

# The config auto-loads it:
grep hermit-dev ~/.orc/config.js
```

Inside an ORC session:

```js
orc.listPlugins();          // includes { name: "hermit-dev", loaded: true, ... }
orc.loadPlugin("hermit-dev");
orc.hermit-dev.status();    // policyLoaded: true, policyBytes > 0
```

## Uninstall

```bash
rm -rf ~/.orc/plugins/hermit-dev
# then remove the `orc.loadPlugin("hermit-dev");` line from ~/.orc/config.js
```
