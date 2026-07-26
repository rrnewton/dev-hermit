# Determinize|Record Boundary Controls — Recreation Design (against the current model)

Author: [impl agent, opus-4.8] (task `impl-record-boundary-design`), 2026-07-25.
Status: **design ready; implementation sequenced after #662** (see "Sequencing").

## Goal

Let users choose, per nondeterminism subsystem, whether Hermit **determinizes**
it (Detcore's normal substitution) or **records** it (captures at record time,
replays back). CLI flags on `hermit record start`:

`--record-time --record-pids --record-sched --record-cpuid --record-rng
--record-fs --record-signals` plus `--record-all`. This is Hermit's unique
"variable boundary" value proposition.

## Why this is a recreation, not a revival

- Prior full implementation = **PR #586** (~1034 lines / 25 files, head
  `c49bb906`, branch `design-record-boundary-slot110`). It is **CLOSED
  UNMERGED**. Coordinator directive (rrnewton, on #586): *"Closing this stale,
  conflicted design draft… current record/replay implementation is being
  consolidated in #662… Any boundary-control proposal should be recreated
  against that current model rather than repairing this abandoned branch."*
- So #586 is a **design reference only**. Do not revive its branch.

## Current model (audited on origin/main @ca10792c)

- **Schema:** `RECORD_VERSION = RecordVersion(0x103)` at
  `hermit-cli/src/metadata.rs:47`; exact-match compat (`compatible_with`,
  `metadata.rs:39-41`) — **any** bump breaks replay. Version is stamped into
  `Metadata` (`metadata.rs:50-70`), written `record.rs:46`, checked
  `replay.rs:61-68`.
- **DetConfig for record/replay is HARD-CODED**, not persisted:
  `record_or_replay_config()` at `metadata.rs:145-215` builds the config for
  both phases; TODO at `metadata.rs:144` says "Record this in the metadata
  instead of hardcoding this." That TODO is the natural home for boundary
  choices. Record mode currently sets `virtualize_metadata:false`
  (`metadata.rs:159`) → **FS is already recorded, not determinized**.
- **The boundary seam already exists architecturally:** `Detcore<T>` is generic
  over `T: RecordOrReplay` (`detcore/src/record_or_replay.rs:20`; T ∈
  {NoopTool, Recorder, Replayer}). Universal delegation point
  `Detcore::record_or_replay()` at `tool_local.rs:261-275` — every syscall
  Detcore partially determinizes forwards to `T`. **Per subsystem, the boundary
  is: call the determinizing `handle_*` vs. forward to `record_or_replay`.**
- **`record start` CLI (`StartOpts`, `record_start.rs:165-204`)** currently has
  NO path to per-subsystem DetConfig; `StartOpts::main`
  (`record_start.rs:307-354`) → `hermit::record*` (`lib.rs:906-922`) →
  `Record::spawn` (`record.rs:35-57`) → `record_or_replay_config` (hard-coded).
  The `--strict` record flag is already a retained no-op (`record_start.rs:171`).

## Per-subsystem determinize-vs-record anchors (from audit)

| Flag | Determinize path (today) | Record path | Notes |
|---|---|---|---|
| `--record-time` | `syscalls/time.rs` handle_gettimeofday/time/clock_gettime/clock_getres (121-195), nanosleep (230-302); rdtsc `lib.rs:826-871`; dispatch gate `lib.rs:1300-1351` (`virtualize_time`) | recorder/time.rs + replayer/time.rs already exist | clock_getres must be included (a #586 review miss) |
| `--record-cpuid` | `lib.rs:774-824` (`virtualize_cpuid`), tables `cpuid.rs`; arch_prctl `syscalls/misc.rs:126-202` | new capture | |
| `--record-rng` | per-thread PRNG `tool_local.rs:861`; getrandom/fill_random_bytes `syscalls/misc.rs:272-378`; AT_RANDOM `lib.rs:1092-1103` | recorder/random.rs:18-36 exists | cover vectored `readv` on /dev/urandom (a #586 review miss) |
| `--record-signals` | `handle_signal_event` `lib.rs:874-924` (ordered via scheduler) | trace `SchedEvent::SignalReceived` | orders *observed* delivery only; cannot synthesize host-originated external signals (documented #586 limit) |
| `--record-sched` | scheduler preemption/schedule record-replay ALREADY exists: `config.rs:144-165`, `should_trace_schedevent()` (492), replay `scheduler/replayer.rs` | existing template | closest existing analog; reuse it |
| `--record-fs` | `virtualize_metadata` `config.rs:95`; `syscalls/files.rs`, `stat.rs`; recorder/replayer fs.rs | **already recorded in record mode** | FS inputs stay captured for loader/file-backed mmap reconstruction (documented #586 limit); this flag mostly formalizes the boundary. **Owned by #662.** |
| `--record-pids` | **greenfield — no virtualization exists.** `lib.rs:594` `DetPid::from_raw(pid) // TODO(T78538674): virtualize pid` (also 937,1048). PIDs are raw namespaced ids; determinism comes from the deterministic PID namespace + scheduler | new work both sides | highest-risk / least-baked; consider deferring to a follow-up |

## Proposed design

1. **Config model** (`detcore-model/src/config.rs`): add
   `pub struct RecordFeatures { time, pids, sched, cpuid, rng, fs, signals: bool }`
   with `all()` and `#[serde(default)]` on every field (forward/back-compat),
   and `pub record_features: RecordFeatures` on `Config`. (Salvaged verbatim
   from #586.) Empty policy = record-mode default (Detcore-managed sources
   determinized; FS captured as today).
2. **Persist in metadata** (`metadata.rs`): store the effective `RecordFeatures`
   (and, honoring the `metadata.rs:144` TODO, ideally the whole record config)
   in `Metadata`; **bump `RECORD_VERSION` ONCE, composed with #662's bump**
   (see Sequencing). Replay reads `record_features` back so it injects exactly
   the subsystems that were captured. Add a serde default so pre-feature traces
   still parse where compat allows.
3. **CLI** (`record_start.rs`): add the seven `--record-*` flags + `--record-all`
   to `StartOpts`; thread them into `record_or_replay_config`/`Record::spawn`
   (the currently-missing path). Remove/keep the `--strict` no-op per #586.
4. **Boundary routing**: in each subsystem's Detcore handler, branch on
   `cfg.record_features.<subsystem>`: if set, forward to
   `self.record_or_replay(...)` (`tool_local.rs:261`) instead of determinizing.
   Reuse existing recorder/replayer subsystem files; add `SyscallEvent` variants
   in `event.rs:64-94` only where a subsystem lacks one (keep the recorder
   `recorder/mod.rs:223-347` and replayer `replayer/mod.rs:113-244` match arms
   identical — the "must be identical" invariant).
5. **Replay fidelity**: recorded schedules must reject desync/exhaustion/unused
   events (a #586 hardening; `expect_syscall` `replayer/mod.rs:350-409`).

## Known limitations to carry forward (validated in #586)

- FS/loader inputs remain **captured** for file-backed mmap + `ld.so.cache`
  reconstruction even under a "determinize FS" intent (replay uses placeholder
  descriptors). Document, don't pretend otherwise.
- `--record-signals` orders observed delivery; does not synthesize
  host-originated external signals.
- `--record-pids` is greenfield (no determinize path today) — highest risk.

## Sequencing (the blocker)

The feature's core changes — `RECORD_VERSION` bump, `Metadata` fields,
`SyscallEvent` variants, recorder/replayer match arms, `files.rs` — are exactly
the surfaces **PR #662** ("Fix record/replay filesystem and descriptor side
effects", OPEN/draft, actively landing, bumps schema 0x103→0x109) is rewriting.
Implementing on today's main would create a **third competing schema bump**
(after #586's 0x104 and #662's 0x109) and collide in the same files — the exact
failure mode that stranded #586.

**Recommendation:** implement **on top of #662** (after it lands on main, or as a
PR stacked on its branch), so there is ONE coordinated schema bump. #662 is
imminent (14 commits, updated 2026-07-25, `human-review`+`post-facto-review`
labels). Do `--record-fs` in coordination with #662's FS owner (or defer it),
and consider deferring the greenfield `--record-pids` to a follow-up.

## Implementation order once unblocked

1. RecordFeatures in Config + serde defaults.
2. CLI flags + threading into record config.
3. Metadata persistence + single coordinated `RECORD_VERSION` bump (with #662).
4. Boundary routing for the low-risk, template-backed subsystems first:
   time, cpuid, rng, sched (sched already has record/replay).
5. signals; then fs (coordinate with #662); pids last (greenfield).
6. Per-flag E2E: `record start --record-<x> --verify` on ptrace; a
   `record_all_captures_boundary_sources`-style test (clock_getres, all pid
   queries, vectored /dev/urandom, cpuid, guest SIGUSR1) as in #586; corruption
   probes (mutated branch count / appended schedule event must fail replay).
