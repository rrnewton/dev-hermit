# safe-ci-dag-runner: a buck2/superconsole-style live progress view

**Task:** `safe-ci-dag-runner-live-progress-buck2` (P2) — study buck2's live progress
indicator (the `superconsole` crate) and design a much better live `validate` view that
shows real-time **PARALLELISM** (which DAG cells are running / queued / done) and
**MACHINE LOAD** (CPU / mem / pressure).
Ties to `ci-dag-parallelize-sub10min`.

**Status:** research + design (read-only survey; no product code changed).
**Author:** impl agent, opus-4.8, 2026-08-02.

---

## 1. Executive summary

`safe-ci-dag-runner`'s live view today is a flat, append-only stream of atomic
`println!` lines — `[tag] ▶ START`, `[tag] ✓ PASS`, `[tag] ✗ FAIL`, `[tag] ⊘ ABORT`
(`agent-utils/rs/safe-ci-dag-runner/src/scheduler.rs`, `fn emit`). At any instant you
cannot see, at a glance, **how many cells are running vs queued vs done**, **how long
each running cell has been running**, or **what the machine is doing** (CPU / memory /
pressure). All of that information already exists in the process — it is just never
surfaced as a live panel.

buck2 solves exactly this problem with its own OSS crate, **superconsole**
(`facebookincubator/superconsole`, MIT/Apache-2.0, Meta). superconsole splits the
terminal into a **scratch/canvas area** (redrawn in place every tick) and an **emitted
log area** (scrolls above), and renders the scratch area from a tree of pure
`Component`s. buck2 stacks a `TimedList` of running actions (each with a live elapsed
timer and color escalation), a summary counts row, and a `SystemWarningComponent`
(memory pressure / disk) into that scratch area.

**Recommendation:** adopt superconsole's *design*, not necessarily its *crate*. A
~150–250-line in-house scratch-area renderer using raw ANSI (the same `cursor-up` +
`clear-to-end` dance superconsole uses) gives us the buck2 look **with zero new
dependencies**, preserving safe-ci-dag-runner's deliberately minimal, async-free,
100%-safe-Rust posture. Depending on `superconsole` directly is documented as
Alternative B, but it pulls in tokio + futures + crossterm + nix — a large tree for a CI
helper binary whose entire current dep set is `serde_json`, `serde_norway`,
`signal-hook`.

The data plumbing is the easy part: **the parallelism state and the machine-load
readers already exist.** This is a presentation-layer feature.

---

## 2. Survey: how superconsole works

Source: <https://github.com/facebookincubator/superconsole> (crate `superconsole`
`0.3.0`, `license = "MIT OR Apache-2.0"`).

### 2.1 The two-region model

From `src/lib.rs`:

> Rendering can be divided into two principal components:
> * In the **scratch** area, the previous content is overwritten at each render.
> * In the **emitted** area, lines scroll away above the scratch with various diagnostic output.
> Components live in the scratch area.

- **Scratch / canvas** — a block at the bottom of the terminal that is *redrawn in
  place* every render tick. This is the live dashboard (running actions, counts,
  system usage).
- **Emitted** — permanent log output (`SuperConsole::emit(lines)`), flushed *above* the
  canvas so it scrolls into scrollback normally. This is where per-action completion
  messages / warnings go.

Crucially, this maps 1:1 onto what safe-ci-dag-runner already has: the current `emit()`
line stream **is** the emitted area; we only need to *add* a scratch area.

### 2.2 In-place redraw mechanics

`src/superconsole.rs`:

- `clear_canvas_pre(writer, height)` — queues `MoveUp(n)` (chunked to `u16`) + `MoveToColumn(0)`
  to walk the cursor back up over the previous canvas.
- draws the new canvas,
- `clear_canvas_post(writer)` — `Clear(ClearType::FromCursorDown)` so a *shrinking*
  canvas doesn't leave stale lines behind.
- `reuse_prefix = canvas_contents.lines_equal(&canvas)` — unchanged leading lines are
  **not** rewritten, which "avoid[s] flickering things like URLs in VS Code terminal."
- `cursor::Hide` around the frame; buffered into one `Vec<u8>` and written in one
  `output()` call to avoid partial frames.

This is precisely the ANSI recipe an in-house renderer would replicate
(`\x1b[{n}A`, `\r`, `\x1b[J`, `\x1b[?25l/h`).

### 2.3 The `Component` trait — pure render over immutable state

`src/components.rs`:

```rust
pub enum DrawMode { Normal, Final }

pub trait Component {
    type Error;
    fn draw_unchecked(&self, dimensions: Dimensions, mode: DrawMode) -> Result<Lines, Self::Error>;
    fn draw(&self, dimensions: Dimensions, mode: DrawMode) -> Result<Lines, Self::Error> {
        let mut res = self.draw_unchecked(dimensions, mode)?;
        res.shrink_lines_to_dimensions(dimensions); // truncate to fit
        Ok(res)
    }
}
```

- **Render is a pure function** of `(dimensions, mode)` plus an *immutable borrow* of
  program state — "each render call accepts an immutable reference to state." State
  mutation and rendering are cleanly separated (very testable).
- `DrawMode::Final` lets a component render its terminal, non-animated form for the last
  frame (e.g. drop the spinner, print totals).
- `Dimensions` = max `(width, height)` the component may use; oversized output is
  truncated, so a component can never corrupt the layout.
- Components compose: `Bordered`, `Bounded`, `Padded`, `Aligned`, `Split`, `Spinner`,
  and `DrawVertical`/`DrawHorizontal` stack children within a height/width budget.

### 2.4 Compatibility / non-tty fallback

`SuperConsole::new() -> Option<Self>`:

```rust
pub fn compatible() -> bool {
    io::stderr().is_tty() && !Self::is_term_dumb() && enable_ansi_support()
}
```

- Renders on **stderr**, so `command > out.txt` (stdout redirect) still gets clean data
  while the TUI paints on the terminal.
- `TERM=dumb` and non-tty → `new()` returns `None`; the caller falls back to a plain
  line printer (buck2 falls back to its `SimpleConsole`).

### 2.5 The caller owns the tick

superconsole does **not** run its own timer. The caller re-renders on a cadence
(buck2's `Ticker`/`Tick`, ~decisecond). A single monotonic `Timekeeper` snapshot per
frame keeps every elapsed timer consistent within a frame.

---

## 3. Survey: how buck2 composes superconsole

Source: `facebook/buck2`,
`app/buck2_client_ctx/src/subscribers/superconsole.rs` and siblings.

buck2's live view is a **vertical stack of `Component`s** drawn via `DrawVertical`:

| buck2 component            | what it shows                                                        |
|----------------------------|----------------------------------------------------------------------|
| `TasksHeader` / `SessionInfoComponent` | session id, elapsed, high-level counts             |
| `SystemWarningComponent`   | **machine load**: memory-pressure & low-disk warnings from snapshots |
| `ReHeader` / `IoHeader`    | remote-execution + I/O state                                         |
| `DiceComponent`            | DICE (build graph) state                                             |
| `TimedList`                | **parallelism**: one row per running action + live elapsed timer     |
| `DebugEventsComponent` / `CommandsComponent` | opt-in debug detail                                |

Key patterns worth stealing:

1. **`TimedList` = the parallelism panel.** One row per in-flight span, each with a live
   elapsed timer. Long-running roots collapse their children as
   `root [child + N]`. A `SummaryRow` gives aggregate done/running/remaining counts.
   (`timed_list.rs`, `table_builder.rs`.)
2. **`Cutoffs { inform, warn }` → color escalation.** A row is green under `inform`,
   yellow past it, red past `warn` — so a stuck cell turns red on its own. This is the
   single highest-value idea for surfacing a wedged CI cell.
3. **`SystemWarningComponent` = the machine-load panel.** buck2 checks memory pressure
   and disk from periodic snapshots (`check_memory_pressure_snapshot`,
   `check_remaining_disk_space_snapshot`) and prints styled warning lines. `TwoSnapshots`
   computes rates (e.g. CPU%) between consecutive ticks.
4. **Non-tty → `SimpleConsole`.** buck2 keeps a plain line-stream subscriber for
   non-interactive / CI-log use. safe-ci-dag-runner *already is* that SimpleConsole.

---

## 4. Where safe-ci-dag-runner is today

### 4.1 The current live view (the gap)

`src/scheduler.rs`:

```rust
fn emit(line: &str) { println!("{line}"); }         // atomic, non-interleaving

emit(&format!("[{tag}] ▶ START  {}", step.desc));    // on launch
emit(&format!("[{tag}] ✓ PASS   ..."));              // on completion
emit(&format!("[{tag}] ✗ FAIL   ..."));
emit(&format!("[{tag}] ⊘ ABORT  ..."));
```

- No in-place redraw, no aggregate panel, no timers, no machine-load line.
- At `-vv`, child stdout/stderr is interleaved into the same stream (`spawn_reader`).
- This flat stream is exactly buck2's `SimpleConsole` role → **keep it verbatim as the
  non-tty / `--no-live` fallback.**

### 4.2 The data already exists (the good news)

**Parallelism state** — `src/scheduler.rs`, `struct Shared` (behind `Arc<Mutex<>>`):

```rust
running: HashSet<String>,          // cells running RIGHT NOW
running_pids: HashMap<String,u32>,
done: HashMap<String,StepOutcome>, // finished (ok/fail/abort)
order: Vec<String>,                // dispatch order → queued = order − done − running − skipped
resource_avail: HashMap<String,i64>,
cores_used: i64,                   // CPA core-budget occupancy
```

Plus `skipped()` (transitive dep-failure closure) and `self.jobs` (max concurrency).
Everything needed for a running/queued/done/cores panel is **already in `Shared`**; a
renderer just needs a read-lock snapshot per tick, plus a per-cell launch `Instant` for
the elapsed timer (one small field to add to `running_pids` or a parallel map).

**Machine load** — `src/ambient.rs` is a pure `/proc` reader that already computes
everything buck2's `SystemWarningComponent` shows and more:

- `/proc/stat` busy jiffies → host CPU%,
- `/proc/loadavg` → `load1`,
- `/proc/pressure/{cpu,memory,io}` → PSI `avg10`/`avg60` (`PsiReading`),
- external-cores estimate, co-tenant count,
- `AmbientBucket::{Quiet,Moderate,Busy}` verdict.

Today `capture_ambient_snapshot` is called only at each step's start/end and folded into
profile rows — **never shown live.** A ticker calling it every ~250 ms *is* the
machine-load panel.

**Per-cell resource use** — `src/cgroup.rs`: `memory.peak`, `memory.events` `oom_kill`,
`cpu.stat`, `cpu.pressure` per step cgroup — available live per running cell.

> **Conclusion:** this is a rendering feature, not a data feature. No new measurement
> code is required; `ambient.rs` + `Shared` + `cgroup.rs` already produce every number.

---

## 5. Design: the safe-ci-dag-runner live view

### 5.1 Target frame (mockup)

Emitted log scrolls above; the boxed canvas is redrawn in place each tick:

```
[build.hermit]   ✓ PASS   12.4s
[test.detcore]   ✗ FAIL   (exit 101) see run-ledger
─── validate ─────────────────────────────────  elapsed 3m12s ───
  running 4/8      queued 11      done 27      failed 1      cores 14/16
  ▶ e2e.kvm-python        1m40s  ███████░░  (warn: >90s)      2.1 GiB
  ▶ build.reverie-release   58s  ████░░░░░                    3.4 GiB
  ▶ test.dbi-corpus         22s  ██░░░░░░░                    0.9 GiB
  ▶ e2e.sabre-exec           4s  █░░░░░░░░                    0.3 GiB
  … +11 queued  (next: test.sizing, build.liteinst, …)
─── machine ──────────────────────────────────────────────────────
  cpu 78%   load1 12.3   mem 61%   PSI cpu/mem/io 14/2/0 (avg10)   [moderate]
  ⚠ memory pressure rising — PSI mem avg10 21% > 20%
```

Design choices (each traces to a buck2 pattern in §3):

- **Summary counts row** (`running/queued/done/failed/cores`) — buck2 `SummaryRow`.
- **One row per running cell** with a live elapsed timer + progress bar (elapsed vs the
  cell's `hint.est_duration_s`, which the DAG already carries) + peak RSS from
  `cgroup.rs` — buck2 `TimedList`.
- **Color/label escalation** via `Cutoffs { inform, warn }` derived from each cell's
  estimate (e.g. `warn = 1.5 × est`): a cell over its estimate turns yellow, way over
  turns red. Directly buck2's escalation idea; the biggest win for spotting a wedged
  cell (cf. the demo5 wedge investigations — a stuck cell would visibly redden).
- **Queued roll-up** `… +N queued (next: …)` — bounded height, buck2's `+N` collapse.
- **Machine panel** from `ambient.rs`: CPU%, load1, mem%, PSI triple, `AmbientBucket`
  verdict, plus buck2-style **warning lines** when PSI crosses the existing
  `BUSY_PSI_AVG10` / `BUSY_EXTERNAL_CORES` thresholds (`SystemWarningComponent`).
- **`DrawMode::Final`** — last frame drops spinners/bars and prints a static summary the
  CI log keeps.

### 5.2 Architecture (superconsole-inspired, in-house)

```
                 ┌──────────────────────────────────────────┐
 scheduler ─────▶│ Shared (Arc<Mutex>)  running/queued/done  │
 threads         └──────────────────────────────────────────┘
                                │  read-lock snapshot per tick
                                ▼
   ambient.rs ──▶ ┌───────────────────────────┐
   cgroup.rs ───▶ │  render::Frame (pure fn)   │  = Component::draw analogue
                  │  state → Vec<String> lines │
                  └───────────────────────────┘
                                │ lines
                                ▼
                  ┌───────────────────────────┐
                  │  render::Canvas            │  = SuperConsole scratch mgr
                  │  in-place ANSI redraw:     │  cursor-up N, clear-to-end,
                  │  diff vs last frame,       │  reuse unchanged prefix
                  │  emit() log ABOVE canvas   │
                  └───────────────────────────┘
```

- A dedicated **ticker thread** wakes every ~250 ms, snapshots `Shared` (brief
  read-lock) + calls `ambient` (cached ~1 Hz), builds a `Frame` (pure, testable), and
  asks the `Canvas` to repaint.
- `emit()` is rerouted: instead of `println!`, completion/warning lines are handed to
  the `Canvas`, which prints them **above** the scratch block (superconsole's
  emitted-area contract) so live rows never interleave with scrollback logs.
- **Pure `Frame` builder** = superconsole's `Component::draw`: `fn(&Snapshot,
  Dimensions, Final) -> Vec<String>`. Unit-testable with zero terminal, exactly
  buck2's testability win.

### 5.3 Non-tty / CI fallback (mandatory)

- Gate on `stderr().is_tty() && TERM != "dumb"` (superconsole's `compatible()`).
- Non-tty or `--no-live` → **the current flat `emit` stream, unchanged.** This preserves
  today's CI-log behavior byte-for-byte (important: `super-validate.sh` /
  `validate.sh` logs are parsed elsewhere; the ledger contract must not shift).
- Render the TUI on **stderr**; keep machine-readable output (profile rows / summary
  JSON) on **stdout** so redirects stay clean — mirrors superconsole rendering on
  stderr.
- A final `DrawMode::Final` frame + the existing end-of-run summary is what lands in
  non-tty logs.

### 5.4 Dependency decision

| Option | New deps | Fidelity | Fit with crate's minimalist ethos |
|--------|----------|----------|-----------------------------------|
| **A (recommended): in-house scratch renderer** | **none** (raw ANSI to stderr) | ~95% of the mockup | ✅ matches "safe Rust, 3 deps" posture |
| B: depend on `superconsole` `0.3` | tokio + futures + crossterm + nix + bytes + crossbeam + tracing + unicode-* | 100%, batteries incl. | ❌ large async tree in an async-free thread-based binary |
| C: `crossterm` only (no superconsole) | crossterm (+ its small tree) | ~98%, portable Win/Mac | ~ moderate; buys Windows/ANSI portability |

`safe-ci-dag-runner`'s `Cargo.toml` deliberately runs on **three** deps
(`serde_json`, `serde_norway`, `signal-hook`) and is thread-based, not async. Pulling
`superconsole` drags in **tokio + futures**, which is disproportionate for a Linux CI
helper (the whole tool is cgroup-v2/`/proc` Linux-specific already, so crossterm's
cross-platform value is low). **Recommend Option A**; the ANSI subset needed is exactly
what §2.2 documents (`MoveUp`, `MoveToColumn(0)`, `Clear FromCursorDown`, `Hide/Show
cursor`) — ≈150 lines, no `unsafe`. Option C is the fallback if we later want Windows
support.

### 5.5 Implementation plan (phased, each independently landable)

1. **`render.rs` (pure) + tests** — `Snapshot` (cloned from `Shared` under one lock) and
   `fn frame(&Snapshot, width, height, final) -> Vec<String>`. Golden-string unit tests,
   no terminal. *(No behavior change; not wired in yet.)*
2. **Add per-cell launch `Instant`** to `Shared` (or a `started: HashMap<String,Instant>`)
   for elapsed timers. Trivial, no output change.
3. **`Canvas` scratch renderer** — the ANSI in-place repaint + `emit-above-canvas`
   reroute, gated behind `is_tty() && !dumb`. Non-tty path unchanged.
4. **Ticker thread** — ~250 ms repaint; `ambient` cached at ~1 Hz to bound `/proc` cost.
5. **Machine panel + warning lines** from `ambient.rs` thresholds
   (`BUSY_PSI_AVG10`, `BUSY_EXTERNAL_CORES`); `Cutoffs`-style row color escalation.
6. **Flags** — `--live=auto|always|never` (default `auto`), `--no-live` alias; document
   in README. `-vv` child streaming stays on the emitted-above-canvas path.

Risks / guards:
- **Do not change stdout / ledger output.** All TUI paint goes to stderr; the flat
  fallback stays byte-identical in non-tty. (Ref memory:
  `validate-run-ledger-reconstruction`.)
- Terminal resize: recompute `Dimensions` each tick (superconsole re-queries size every
  frame; do the same).
- Lock hygiene: the ticker takes only a short read-snapshot; never hold the `Shared`
  lock across I/O.

---

## 6. Source pointers

- superconsole crate: <https://github.com/facebookincubator/superconsole>
  - two-region model + render/diff: `src/lib.rs`, `src/superconsole.rs`
    (`render_general`, `clear_canvas_pre/post`, `reuse_prefix`).
  - `Component` / `DrawMode`: `src/components.rs`.
- buck2 usage: <https://github.com/facebook/buck2>
  - subscriber wiring: `app/buck2_client_ctx/src/subscribers/superconsole.rs`
  - parallelism panel: `.../superconsole/timed_list.rs` (+ `table_builder.rs`, `Cutoffs`)
  - machine-load panel: `.../superconsole/system_warning.rs`
- safe-ci-dag-runner (this workspace, `rrnewton/agent-utils`):
  - current live view: `rs/safe-ci-dag-runner/src/scheduler.rs` (`fn emit`, `run_step`)
  - parallelism state: `src/scheduler.rs` (`struct Shared`)
  - machine load: `src/ambient.rs`; per-cell resource: `src/cgroup.rs`
  - static DAG viz (unrelated but adjacent): `src/viz.rs`

---

## 7. Bottom line

Everything needed for a buck2-quality live `validate` view already lives in the process:
`Shared` holds running/queued/done/cores, `ambient.rs` holds CPU/load/PSI, `cgroup.rs`
holds per-cell RSS. The missing piece is a **superconsole-style scratch area** — a small
pure `Frame` builder plus an in-place ANSI `Canvas` and a ~250 ms ticker — which can be
built with **no new dependencies**, keeping the crate's minimalist, safe-Rust,
thread-based design intact, while the existing flat line stream remains the untouched
non-tty/CI fallback. The single highest-value idea to port from buck2 is **elapsed-time
color escalation** (`Cutoffs`): a wedged cell reddens on its own, which would have made
several of the demo5-class scheduler-wedge investigations obvious at a glance.
