# ORC spawn conflates CLI-type with model-selection

**Root-cause analysis for the ORC owner. Analysis only — no ORC internals were modified.**

| | |
|---|---|
| Author | `hermit-w5` (opus-5), 2026-08-06 |
| Task | `spawn-conflates-cli-type-with-model-selection-claude-got-gpt-model` |
| Symptom | live cmdline `claude --model luna --permission-mode acceptEdits` |
| ORC build | `orc=f08c5eac0082` (`/home/newton/orc-bin/orc`) |
| Source read | `/home/newton/work/orc-dev/fbsource/fbcode/orc/` (fbsource `3802980afacc`) |
| Verdict | **Confirmed. ORC has no model↔CLI-type validation anywhere on any spawn path.** |

---

## 1. The finding in one sentence

ORC treats the **CLI type** as a typed, validated enum and the **model** as an
untyped opaque string that it never reads — so `{cli: "claude", args: ["--model",
"gpt-5.6-sol"]}` is accepted by every layer and fails only inside the spawned
binary, in a tmux pane, minutes later, as `model may not exist`.

There is no bug to point at in the sense of a wrong line. **The defect is a missing
relationship:** two things that must agree are represented in ways that cannot be
compared.

## 2. Where each half is decided

### CLI type → binary: typed, validated, single source of truth

`dg/src/cli_agent.rs`

```rust
pub enum CliType { Claude, Codex, Devmate, Gemini, Metacode, Orc, Pi }   // :227

pub fn binary_name(&self) -> &'static str {                              // :255
    match self {
        CliType::Claude => "claude",
        CliType::Codex  => "codex",
        CliType::Devmate => "dm",
        ...
    }
}

pub fn from_name(name: &str) -> Option<CliType> { ... }                  // :283
```

The string→enum step is validated: `resolve_cli_spawn_target`
(`orc-engine/src/effects/agent_backend.rs:833`) rejects unknown CLI names and
explicitly rejects `cli: "orc"` (`:845`). `resolve_cli_command`
(`dg/src/tmux/backend_impl.rs:41`) additionally enforces that a plugin *flavor*
matches its `base_cli`. **So the CLI half is well guarded.**

### Model → flag: ORC never constructs it, never reads it, never checks it

`dg/src/tmux/backend_impl.rs:68` is the *entire* argv-augmentation logic:

```rust
pub(crate) fn cli_args_for_spawn(name, role, target) -> Vec<String> {
    let mut args = target.args.clone();          // caller's raw args, verbatim
    match target.cli {
        CliType::Claude => args.extend(["--permission-mode", "acceptEdits"]),   // :77
        CliType::Codex  => args.extend(["--sandbox","workspace-write",
                                        "--ask-for-approval","on-request"]),
        _ => {}
    }
    if let Some(extra) = extra_cli_args_from_env(target.cli) { args.extend(extra); } // :111
    if let Some(role) = role { /* claude-only --agents/--agent */ }
    args
}
```

`dg/src/tmux/spawn.rs:41` then shell-quotes and joins with **no inspection of arg
content**. `SpawnCliOpts` (`dg/src/backend_trait.rs:136–152`) **has no `model`
field at all** — only `cli`, `flavor`, `args: Vec<String>`, `read_only`, `role`,
`cwd`, `remote`, `workspace_id`, `timeout_ms`, `transport`. The JS-side validator
`opt_string_array` (`orc-engine/src/js/effects/agents.rs:91`) checks only
"is an array of strings".

A repo-wide search for `allowed_models|MODEL_ALLOWLIST|valid_models|validate_model`
under `fbcode/orc` returns **zero hits on any agent-spawn path**. The one
`ModelRegistry` (`llm-inference/src/model_config.rs:712`) governs ORC's *own*
internal inference and is disjoint from CLI-agent spawning. The only typed `model`
field anywhere is on the dm-core path (`dg/src/backend_trait.rs:218`), and it is
forwarded to an HTTP service — validation, if any, is server-side.

## 3. The argv proves which path ran

The observed cmdline is precisely reconstructible:

```
claude          --model luna          --permission-mode acceptEdits
└ binary_name() └ target.args verbatim └ the Claude arm of cli_args_for_spawn:77
```

`--permission-mode acceptEdits` appears **after** `--model luna`, which is exactly
`cli_args_for_spawn`'s ordering (caller args, then the per-CLI append). So this
went through the normal ORC tmux spawn path with `cli = Claude` and caller-supplied
`args = ["--model", "luna"]`. It was not a hand-typed pane.

## 4. Three independent channels can inject a model, none validated

1. **Caller `args`** — the one that fired here. Plugin code builds arrays like

   ```ts
   function sharesheetVmCodexArgs(): string[] {          // plugins/ml-pipeline/agents.ts:108
     return [...codexAutoReviewArgs(), "--model", "gpt-5.6-sol",
             "--config", 'model_reasoning_effort="xhigh"'];
   }
   ```

   Note the shape of the hazard: the array is *named* for its CLI (`...CodexArgs`)
   but its **type is `string[]`**. Nothing ties it to `cli`. Pair it with
   `cli: "claude"` and every layer agrees.

2. **`--claude-args` / `--codex-args` at ORC launch**, which land in
   `extra_cli_args_from_env` (`backend_impl.rs:111`) and are appended, whitespace-split
   and unvalidated, to *every* spawn of that CLI type. Historical launches on this box
   routinely carried a model here:

   ```
   exec orc --db atropos \
     '--claude-args=--dangerously-skip-permissions --dangerously-enable-internet-mode --model=claude-opus-4-8 --effort=high' \
     '--codex-args=...'
   ```

   (`orc_cli_lib::launch::tmux`, 2026-06-05/07-13/07-21/07-22.) The current `hermit`
   session carries no `--model` in `--claude-args`, so this channel is *not* the live
   cause — but it is a standing crossing hazard: two sibling strings, one per CLI,
   distinguished only by their flag name.

3. **Pane adoption.** `CliType::from_command` (`cli_agent.rs:304`) classifies an
   existing tmux pane by **substring word-match over the whole command line**, in a
   fixed priority order (`claude`, then `metacode|opencode`, then `codex`, …). It
   never looks at `--model`. Any pane whose command line contains the word `claude`
   anywhere is adopted as a Claude agent — including, e.g., a codex pane carrying a
   `--config` value that mentions claude.

## 5. What I could and could not establish about the origin

**Could:** the argv shape (§3) proves the spawn went through `cli_args_for_spawn`
with `cli = Claude` and `--model luna` in the caller's `args`.

**Could not:** identify the calling code. Across every retained ORC log
(2026-06-05 → 2026-08-06), **zero** `spawnCliAgent` / `rawSpawnOrcAgent` /
`spawnDmCoreAgent` records name `luna`, `tara`, or `soul`. Every logged
`spawnCliAgent` in this session has the shape
`{cli: "claude"|"codex", cwd: ..., transport: ...}` with **no `args` key at all**.
The only log lines naming these agents are the coordinator's own gchat messages
*about* the bug, plus later `dg::tmux::sync` state transitions
(`test-luna` pane `%12`, `test-luna2` pane `%15`) and scuba `tool_call` events —
i.e. ORC observing agents that already existed.

So the spawn used a path that is not the JS `spawnCliAgent` effect. The two
candidates, both of which reach the same `cli_args_for_spawn`, are the `dg` CLI
(`dg/src/main.rs:841 cli_spawn_target_from_command`) and `orc summon`.

**Discriminator for whoever finishes this:** those paths do not log their arg
vector. Adding one `INFO` line that logs the *resolved* `(cli, binary, args)`
immediately before `build_spawn_command` would have answered this in seconds, and
is worth doing regardless of the fix below — see Recommendation 4.

## 6. Why this produced *dead* agents rather than a loud error

Both faults compounded, and neither is observable at spawn time:

1. **Wrong harness.** A codex/GPT model name was routed to the `claude` binary.
   Claude has no `luna`, so it exits with `model may not exist`.
2. **Typo'd name.** `luna` / `tara` / `soul` are voice-transcription corruptions of
   `terra` / `sol` / `gpt-5.6-sol`.

ORC's spawn returns success — it launched a process. The failure surfaces inside a
tmux pane as an agent that never becomes ready, which the fleet reads as
"agent died, no upstream". **A spawn that cannot fail at spawn time cannot be
retried intelligently**, which is why five agents stayed dead.

## 7. The correct separation

**CLI type selects the binary. The model is a property of that binary and must be
validated against it. The two must not be independently settable strings.**

Concretely, `--model` should stop being an anonymous element of `args: string[]`
and become a typed field whose legality is a function of `cli`:

```rust
pub struct SpawnCliOpts {
    pub cli: Option<String>,
    pub model: Option<String>,   // NEW: typed, not smuggled through `args`
    pub args: Vec<String>,       // everything else, still opaque
    ...
}

impl CliType {
    /// The models this binary accepts. `None` = unconstrained (no registry yet).
    fn allowed_models(&self) -> Option<&'static [&'static str]>;
}
```

with one check at `resolve_cli_spawn_target` — the point where the CLI string is
already being validated, so the model check sits beside its sibling:

```rust
if let (Some(model), Some(allowed)) = (&opts.model, cli.allowed_models())
    && !allowed.contains(&model.as_str())
{
    return Err(BackendError::ModelNotValidForCli { cli, model, allowed });
}
```

### Recommendations, in priority order

1. **Reject the pairing; do not auto-correct it.** The task suggested selecting a
   GPT model could *force* `cli:codex`. I recommend against it: `luna` was *also a
   typo*, so auto-correction would have silently spawned a codex agent with a
   nonexistent model — a second dead agent and a harder diagnosis. **Fail closed,
   name both the offending model and the allowed set in the error.** Auto-correction
   turns a caught error into a quieter one.

2. **Make the model typed (above).** This is what removes the whole class rather
   than the instance.

3. **Sweep `args` for a smuggled `--model` during a deprecation window.** Even with
   (2), existing callers pass `["--model", X]` inside `args`. Have
   `cli_args_for_spawn` detect `--model`/`--model=` in `target.args` and route it
   through the same validation, warning that the typed field should be used. Without
   this, (2) validates only new callers and the old hole stays open.

4. **Log the resolved spawn.** One `INFO` at `build_spawn_command` with
   `(agent, cli, binary, args)`. §5 is unanswerable today purely because this line
   does not exist.

5. **Validate `--claude-args`/`--codex-args` at ORC launch** against the same
   allowlist, and fail at startup rather than at every subsequent spawn. A bad model
   there poisons every agent of that type for the whole session.

6. **Tighten `CliType::from_command`.** Match the **command word** (argv[0] basename),
   not any word anywhere in the command line. Priority-ordered substring matching over
   a full command line will misclassify panes whose flags mention another CLI's name.

### How to verify a fix (bracket it both ways)

- **Negative:** `spawnCliAgent(name, role, {cli: "claude", args: ["--model", "gpt-5.6-sol"]})`
  must be **rejected at the spawn call**, before any process starts. Assert no pane is
  created — an error that still leaves a dead agent behind is not a fix.
- **Negative:** the same with a name that is not a model of *any* CLI (`luna`) must
  also be rejected, and must **not** be silently retargeted to codex.
- **Positive:** `{cli: "claude", args: ["--model", "<a real claude model>"]}` must still
  spawn, and `{cli: "codex", args: ["--model", "gpt-5.6-sol"]}` must still spawn.
  State both counts — a validator that rejects everything passes the negatives and is
  useless.

## 8. Evidence index

| Claim | Where |
|---|---|
| CLI→binary map | `dg/src/cli_agent.rs:255` |
| CLI name validation | `orc-engine/src/effects/agent_backend.rs:833`, `:845` |
| argv construction, whole of it | `dg/src/tmux/backend_impl.rs:68–109` |
| Claude `--permission-mode acceptEdits` append | `dg/src/tmux/backend_impl.rs:77` |
| env extra-args injection | `dg/src/tmux/backend_impl.rs:111` |
| shell assembly, no content inspection | `dg/src/tmux/spawn.rs:41–81` |
| `SpawnCliOpts` has no `model` | `dg/src/backend_trait.rs:136–152` |
| JS arg validation is type-only | `orc-engine/src/js/effects/agents.rs:91` |
| pane adoption classifier | `dg/src/cli_agent.rs:304` |
| a model in `--claude-args` historically | `orc_cli_lib::launch::tmux`, `~/.orc/logs/`, 2026-06-05 / 07-13 / 07-21 / 07-22 |
| plugin builds `["--model","gpt-5.6-sol"]` as bare `string[]` | `plugins/ml-pipeline/agents.ts:112` |
| no spawn-effect record names luna/tara/soul | all of `~/.orc/logs/*.log`, 2026-06-05 → 2026-08-06 |
