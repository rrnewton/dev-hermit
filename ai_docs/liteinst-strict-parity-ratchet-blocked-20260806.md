# LiteInst strict-parity ratchet: blocked at step 0 by an activation regression

- **Task:** `ratchet-liteinst-strict-parity` (north star: `vision-strict-compat-envelope-to-100`).
- **Author:** impl agent, claude-opus-5. Local only, no egress, no concurrent validate.
- **Tree:** slot `worktrees/oci/hermit` @ `5562161a4`, base hermit main `b64d893ae9ea`.
- **Headline:** the corpus could not be run, because **`--backend liteinst` does not activate at all on
  current main** — and the compat scorecard shows it passing 136 cells at an **ancestor** commit. This
  is a regression, not a known limitation. Separately, the existing scorecard's `parity` column means
  **stdout+exit**, so it cannot support a "maximally-strict parity" claim even where it reads 1.

---

## 1. Attribution control (read this first)

My slot commit touches only `image_container` plus two new `oci`/`podman_store` modules.
`git diff HEAD~1 -- container.rs | grep default_container` is **empty**, so the non-image path every
backend run uses is untouched, and `--backend ptrace` works in the same binary. Nothing below is
attributable to my diff. The failure also reproduced on the pre-commit binary copied from the primary.

## 2. LiteInst does not activate on `b64d893a`

```
$ hermit run --backend liteinst -- /bin/echo hello
Error: verify LiteInst runtime activation failed for tracee N:
       tracee terminated before the required preload handshake completed (phase Waiting)
```

The guest emits **zero stdout** — it dies before the program runs. Reached after following **both**
documented recipes exactly:

| Recipe | Source | Result |
|---|---|---|
| stage debug runtime, `cargo build -p hermit --bin hermit` | `README.md:139-145` | handshake failure |
| `cargo build --release --locked -p hermit --features third-party-backends`, stage **release** runtime | `validate.sh:4757-4770` (authoritative) | **handshake failure** |

Ruled out by measurement:

- the DSO loads fine standalone — `LD_PRELOAD=<so> /bin/echo preload-ok` → rc 0;
- `nm -D` shows `reverie_liteinst_initialize`; `INIT_ARRAY` present;
- hermit's own validator *accepts* the staged DSO (it rejects a wrong one — see §3);
- `--base-env host` does not help;
- `--backend ptrace` succeeds in the same binary.

Failure site: `reverie-ptrace/src/task.rs:4590` — the run loop completes but
`LiteinstRuntimePhase` never advances past `Waiting` to `Ready`.

### It is a regression, and here is the window

`compat-envelope/scorecard.csv` records **136 passing liteinst cells** at hermit `464cbd9f9bb4`
(run ids `liteinst-spst-1785620995`, `liteinst-fullcorpus-1785621912`). `464cbd9f9bb4` **is an
ancestor** of `b64d893a` (`git merge-base --is-ancestor` → rc 0). So liteinst worked at an older
commit on this corpus and does not activate at the newer one.

**Bisect window: 22 commits touching `*liteinst*` between `464cbd9f9bb4` and `b64d893ae9ea`.** Prime
suspects are the reverie pin bumps in that range — `39f084ea4` (pin → `9470712a`), `525627bed`,
`15356709d`, `c008e0146` — plus `b7960a15c` "Remove obsolete detcore-liteinst manifest" and
`a6f8f0e60` "Restore internal LiteInst staging versions".

> **UNVERIFIED, and marked as such:** I did **not** build and run `464cbd9f9bb4` myself. "Known good"
> rests on the scorecard rows, which are a prior agent's recorded claim, not my measurement. The
> first step of any follow-up must be to reproduce green at `464cbd9f` — "premise refuted" is a valid
> outcome. Each bisect step costs a release + `third-party-backends` build (~7 min cold) plus a
> re-stage, so ~5 steps ≈ 40 min.

## 3. Five setup traps (all reusable; one is a guard working correctly)

1. **libunwind evaporates.** `/tmp/lu` from earlier notes was reaped by `/tmp` cleanup mid-session and
   every hermit binary stopped starting. Use a durable prefix — I moved to
   `~/.local/hermit-deps/lu`. Same for cmake (`~/.local/hermit-deps/cm`), which
   `--features third-party-backends` needs for DynamoRIO.
2. **The liteinst cdylib is not refreshed by `cargo build -p hermit --bin hermit`.**
   `target/debug/libreverie_liteinst.so` was 3 days stale against the current pin. Same class as the
   known DBI-cdylib staleness.
3. **`cargo build -p reverie-liteinst` produces a constructor-free DSO**, which hermit **correctly
   refuses**: *"does not register `reverie_liteinst_initialize` as a preload constructor; build the
   locked liteinst-runtime-build manifest"*. This is a **REAL fail-closed guard** doing its job — it
   declined to run degraded and emitted an actionable message. Credit where due.
4. **Ordering trap.** `scripts/stage-liteinst-runtime.sh` writes
   `target/<profile>/libreverie_liteinst.so`, but a later `cargo build -p hermit` **recompiles
   `reverie-liteinst` and regenerates that same path**, clobbering the staged DSO. README says
   stage-then-build; the safe order is **build-then-stage**, or re-stage after every hermit build.
5. **`--backend liteinst` is accepted even when the feature is off.** The enum value is present
   without `--features third-party-backends`, so the CLI takes the request and fails deep inside
   activation instead of saying "this build has no third-party backends". *The accepted-flag surface
   does not carry the built-feature condition* — a proxy-binding defect worth filing separately: the
   flag is a proxy for a capability it does not check.

## 4. The scorecard's `parity` cannot support the north star

`compat-envelope/scorecard.csv`, liteinst rows:

| | count |
|---|---:|
| liteinst rows | **220** |
| `cell_state` = `disabled` | **220 (all)** |
| outcome pass / skip / fail | 136 / 72 / 12 |
| `parity` 1 / 0 / blank | 125 / 23 / 72 |
| ptrace rows (all pass) | 99 |
| distinct `test_id` in file | 249 |

Every liteinst row's `reason` states the parity basis explicitly, e.g.
`parity=exit+stdout vs ptrace` and `parity=bitwise stdout+exit vs ptrace after 3 strict runs`.

**So `parity=1` means stdout and exit status matched — not detlog, not heap, not INFO.** The north
star asks for full `--detlog-stack` / `--detlog-heap` / INFO parity against ptrace. **No row in the
corpus measures that.** A "125 parity" headline therefore overstates strictness relative to the goal;
the honest reading is *125 cells match ptrace on stdout+exit, and detlog parity is unmeasured across
the entire corpus (denominator 220).*

Note also all 220 rows are `cell_state: disabled`, so these cells are not gating anything today.

## 5. Host control: L2 is unattainable here regardless of backend

Independently reproduced earlier this session: `hermit run --strict --verify --verify-strict
--verify-json … -- /bin/echo hi` returns `verdict: diverged`, `bitwise_parity: false` **with no image
and on the ptrace backend**. Default `--verify` (Stripped comparator) passes. So even with liteinst
running, an L2 cross-backend claim could not be established on this box today; the achievable ceiling
is L1 plus an explicit cross-backend detlog diff done by hand. Any liteinst `--verify-strict` failure
observed here is **not attributable** to liteinst.

## 6. Gaps to file

| # | Gap | Evidence |
|---|---|---|
| G1 | **LiteInst backend does not activate on main** (`phase Waiting`); regression vs ancestor `464cbd9f` | §2 |
| G2 | **No detlog/heap/INFO cross-backend parity exists anywhere in the corpus** — parity is stdout+exit for all 220 liteinst rows | §4 |
| G3 | `--backend liteinst` accepted without `--features third-party-backends`; fails deep instead of refusing early | §3.5 |
| G4 | Staging/build ordering clobbers the staged DSO; README recipe has it backwards | §3.4 |
| G5 | liteinst cdylib not rebuilt by the hermit binary build (stale-DSO trap) | §3.2 |

**Recommended order:** G1 (nothing else is measurable until it runs) → G3/G4/G5 (cheap, prevent the
next agent losing the same hour) → G2 (the actual north-star work: a cross-backend detlog comparator,
which does not exist today and is the real deliverable behind "not just stdout/exit").

## 7. What I did not do

- **No corpus run**, because the backend does not start. No parity numbers are reported.
- **No bisect** of the 22-commit window (context budget); the window and suspects are recorded instead.
- **No code changes** to backend or parity logic.
- **Accidental primary write, disclosed:** one `cargo test` invocation ran with the working directory
  at the primary `hermit/` (a `cd` into the slot silently failed), starting a build that wrote
  `hermit/target/debug/build/reverie-dbi-763f7aa617dbe5de/` before failing on cmake. The primary's
  **source tree is unmodified by me** (`git status --untracked-files=no` shows only `README.md` and
  `validate.sh`, which are another agent's concurrent edits — I touched neither) and it remains on
  `main`. Only a build-artifact directory in `target/` was created.
