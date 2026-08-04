# Flaky-failure attribution procedure

**Owner ask (verbatim):** *"When you get a 1/10 failure like that, look hard at
the logs for the failed one and try to ascertain if it was some kind of
infrastructure failure, or non-determinism in Hermit itself, or some other cause.
We need to get better at attribution in the case of flaky failures."*

**Deliverable:** a documented attribution procedure **plus** the harness changes
that preserve failing-run evidence. Every future flake report must state its
ATTRIBUTION and the evidence for it, not just a ratio.

This doc is the procedure. The tooling that implements it is in
`ci-hub/attribution/` (`attribution.py`, `capture-run.sh`, `README.md`); the
harness wiring is in `ci-hub/stress/{stress-burst,nightly.sh}` and
`experiments/multisect_detcore_misc_20260803/matched.sh`.

---

## 0. Why a rate is not an answer

A flake **rate** and a flake **cause** are orthogonal. The same "1/10" is
produced by three bugs that demand *opposite* fixes:

- **INFRASTRUCTURE** — the test/host, not the product. A reverie check that
  wedged 2h44m against a ~2-min baseline; a PMU-sensitive assertion on a
  contended runner; hermit-kvm unable to *measure* anything at ~470 concurrent
  `hermit` processes. **Fix the runner or shed load. Do NOT change product code.**
- **HERMIT_NONDETERMINISM** — a genuine product determinism bug, e.g. the
  `detcore_misc` vfork-reap race (measured 16–23%; fixed in reverie #355). **The
  single highest-value class of fix we ship.**
- **ENVIRONMENT** — the guest read something from the host that varies:
  `/sys/module/<m>/refcnt`, time, entropy, meminfo, load. **Determinize /
  virtualize that read.**

Mis-attribution is expensive in *both* directions: "harden" a real determinism
bug and you paper over the product's core value; dismiss an infra wedge as a
product bug and you burn days in `scheduler.rs` chasing a runner problem. The
job of this procedure is to make the attribution **evidence-driven and
reproducible** rather than a guess.

---

## 1. THE PREREQUISITE: the failing run's artifacts must survive

**You cannot attribute what you did not keep.** Our harnesses overwhelmingly use

```bash
( timeout "$T" cmd args >/dev/null 2>&1; echo $? >> exitcodes )
```

which throws away *everything but an integer* the instant the run ends. By the
time a human notices "3/64 hung", the stdout, the trace, and — most
irreplaceably — **the host conditions at the moment of failure** are gone.
Attribution then degenerates to re-running and hoping.

So the first, non-optional step is to **capture on failure**. Preserve, for each
failing run:

- full **stdout** and **stderr**,
- the **exit code** and whether it was a **timeout**,
- **wall time** vs the known baseline,
- the **host conditions at that instant**: 1-min load, concurrent-process count
  (matching `hermit`), CPU and memory PSI,
- where applicable, a `--log INFO` **trace** (and `--detlog-stack/--detlog-heap`
  for L3), captured by re-running the exact command with logging on.

Tooling: `ci-hub/attribution/capture-run.sh` (pure bash, safe in hot loops under
the BpfJailer exec-rate enforcer) and `attribution.py capture` (richer, for
non-hot-path use) both write an identical **bundle**:

```
<label>-<stamp>-<pid>-<rand>/
  ├── stdout
  ├── stderr
  └── meta.json     # exit_code, timed_out, wall_s, host_before/after, external_reads, shape
```

On **success** they discard, matching the old `>/dev/null` footprint, so turning
capture on costs disk only for the failures you actually want to study.

---

## 2. THE FIVE ATTRIBUTION SIGNALS

Gather these from the bundle (and, for divergence, from two `--log info` runs).
Each maps toward a cause.

### (a) Host conditions at failure
Load average, concurrent-process count, cgroup/PSI pressure — **sampled at the
moment of the failing run**, not now. High load + a stampede of `hermit` procs +
a *hang* shape strongly suggests INFRASTRUCTURE. A quiet host that still fails
rules infra out. This is the signal every prior harness lacked.

### (b) Failure shape
- **Silent hang past the baseline** (exit 124) with no forward progress →
  infra-shaped (starved runner) **or** a scheduler wedge. Disambiguated by (e).
- **Trace/detlog byte-diff** between two runs → hermit-shaped (see (c)).
- **Panic / fatal signal** (SIGSEGV/SIGABRT) → crash; deterministic-at-low-load
  ⇒ hermit; host-read-driven ⇒ environment.
- **Plain nonzero** with no marker → weakest signal; needs (c)/(e).

`attribution.py` computes the shape from exit code + stream text
(`SHAPE_HANG/MISMATCH/CRASH/NONZERO/HARNESS/PASS`).

### (c) Divergence point (the localizer)
Capture two `--log info` runs and run `hermit log-diff a.log b.log`. **The first
divergence is the one that matters; everything after is downstream noise.**
- First diff is a **COMMIT** line — the `(turn, dettid)` schedule reordered →
  **HERMIT_NONDETERMINISM.** Thread-interleaving nondeterminism. This holds *even
  under load*: a load-dependent hermit race and an infra hang both need load, but
  only the product bug reorders the schedule.
- COMMITs match, a **DETLOG** value differs → the schedule is stable but a
  syscall returned different data. Now apply (d).

Use `--skip-detlog` / `--skip-commit` to separate a *scheduling* divergence from
a *data* divergence; `--syscall-history N` for the lead-up context.

### (d) External reads
Scan the trace/stderr for the guest reading a **varying host resource**: `/sys/`,
`/proc/*` (except `self/maps`, which is hermit's own deterministic bookkeeping),
`clock_gettime`, `gettimeofday`, `getrandom`, `rdtsc`, `cpuid`, `sysinfo`,
`uname`, `meminfo`, `loadavg`, `refcnt`. A DETLOG divergence whose value **looks
like a live host reading** → **ENVIRONMENT** (an unvirtualized source). This is
the `/sys/module/refcnt` case. `attribution.py scan_external_reads` /
`--include-detlogs` implement the scan.

### (e) Reproducibility under quiet conditions — THE DECISIVE TEST
Re-run the **exact same command K times at low load**. This one probe settles the
INFRASTRUCTURE-vs-product question that all the others only hint at:
- **Clean at low load, and host was under pressure at failure → INFRASTRUCTURE.**
- **Still fails at low load → NOT infrastructure** (a real hermit wedge or a
  deterministic product bug).

`attribution.py attribute <bundle> --low-load-control K` runs this control and
folds it into the verdict. Without it, an honest hang is `INDETERMINATE` — and
the tool *tells you to run exactly this*.

---

## 3. THE DECISION TREE

```
START: a failing-run bundle (+ optionally two --log info traces)
│
├─ harness token in output (BUILD_FAIL/NOBIN/NOTEST/…)?
│     └─ YES → HARNESS_ERROR. The harness broke; not a flake. Fix the harness.
│
├─ two traces available → run `hermit log-diff`:
│     ├─ first diff is a COMMIT line (schedule reordered)
│     │     └─ HERMIT_NONDETERMINISM (high). Localize in scheduler.rs / relaxations.
│     └─ COMMITs match, a DETLOG value differs
│           ├─ value looks like a live host read (time/rand/sysfs/meminfo/…)
│           │     └─ ENVIRONMENT (high). Virtualize that read.
│           └─ otherwise
│                 └─ HERMIT_NONDETERMINISM (medium). Data divergence, source TBD.
│
├─ shape == HANG (timeout, no divergence)
│     ├─ low-load control run?
│     │     ├─ clean at low load  +  host was pressured at failure → INFRASTRUCTURE (high)
│     │     ├─ clean at low load, no measured pressure           → INFRASTRUCTURE (medium)
│     │     └─ still fails at low load                            → HERMIT_NONDETERMINISM
│     └─ NO control → INDETERMINATE, prescribing: re-run K× at low load; if it
│                      still hangs, capture --log info and look for a scheduler wedge.
│
├─ shape == CRASH (panic / fatal signal)
│     ├─ deterministic at low load (fails every quiet run) → HERMIT_NONDETERMINISM
│     ├─ external host reads present in trace              → ENVIRONMENT
│     └─ else                                              → INDETERMINATE (get a --log info trace)
│
├─ shape == MISMATCH (verify said "nondeterministic")
│     ├─ external host reads present → ENVIRONMENT
│     └─ else → INDETERMINATE, prescribing: two --log info runs + `hermit log-diff`
│
└─ shape == NONZERO / other → INDETERMINATE, prescribing the next probe.
```

Every leaf that is not a firm cause **names the next probe** — an
`INDETERMINATE` is a routed investigation, never a shrug.

---

## 4. REPORTING CONTRACT

From now on, a flake report is incomplete if it is only a ratio. State:

1. **ATTRIBUTION**: one of INFRASTRUCTURE / HERMIT_NONDETERMINISM / ENVIRONMENT /
   HARNESS_ERROR / INDETERMINATE, with confidence.
2. **EVIDENCE**: which of the five signals supported it — host conditions at
   failure, failure shape, the first divergence line, the external read, and/or
   the low-load control result. Bind evidence to a **bundle path or SHA**, not a
   branch name.
3. **NEXT STEP**: the remediation for that cause (they are opposite across
   causes), or, for INDETERMINATE, the decisive probe to run next.

Example (good): *"tests_misc::vfork flaked 3/64 on nightly @6f0c26de. ATTRIBUTION:
2 INDETERMINATE hangs (host contended: load1≈78, 471 concurrent hermit procs, no
low-load control yet) + 1 ENVIRONMENT (trace shows guest read
`/sys/module/nf_conntrack/refcnt`). NEXT: run the 2 hangs 10× at low load to
split INFRASTRUCTURE from a wedge; virtualize the refcnt read. Evidence:
ignored/ci-hub/stress-capture/…-6f0c26de-…/."* — vs the old *"vfork is 5% flaky."*

---

## 5. WHAT WAS SHIPPED

- `ci-hub/attribution/attribution.py` — capture primitive + pure classifier +
  `capture`/`attribute`/`report`/`selftest` CLI. 27 unit tests
  (`tests/test_attribution.py`) encode the three real examples so a regression
  that re-blurs the causes fails mechanically.
- `ci-hub/attribution/capture-run.sh` — pure-bash bundle-emitting wrapper for hot
  loops (no per-instance Python; BpfJailer-safe). Capped with logged drops.
- `ci-hub/attribution/README.md` — tool usage.
- Harness wiring (env-gated `STRESS_CAPTURE_DIR`, default path byte-identical):
  `ci-hub/stress/stress-burst`, `experiments/multisect_detcore_misc_20260803/matched.sh`
  preserve a bundle per failing instance; `ci-hub/stress/nightly.sh` folds
  `attribution.py report` into every P0 alarm (new `attribution` field in the
  marker JSON + a `*.attribution.txt` sidecar).

## 6. Related

- `hermit/.claude/skills/hermit-debugging/SKILL.md` — how to read COMMIT/DETLOG,
  `hermit log-diff` flags, the L1–L4 assurance ladder.
- Memory: `nightly-stress-harness`, `detcore-misc-vfork-flaky-timeout-under-load`,
  `load-dependent-timeslice-skid-pmu-counter`, `scheduler-vtime-jump-unproductive-pollers`.
