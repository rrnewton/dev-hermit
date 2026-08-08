# Corpus-wide can-it-fail sweep: backend-parity C fixtures

**Date:** 2026-08-06 · **Host:** devbig014 · **Task:**
`mutation-test-the-fixtures-can-they-fail` (#322/#323 certification)

## Question

A fixture that passes but **cannot fail** tests nothing: it occupies the slot
where real coverage would go and reports success forever. For each fixture in
the landed backend-parity C corpus, plant a deliberate violation of the exact
property it checks and confirm the fixture's consumer can see it.

## Scope, and why it does not overlap the other sweeps

Derived from the repo, not from a note: 75 `.c` fixtures in
`hermit/tests/backend-parity/fixtures/` on hermit `main` @ `f89c6976`.

**Zero overlap with the 19 items already swept** (`hermit-det3`): those were the
fixtures *added on 2026-08-06*, which live on unlanded landing branches, plus
five parent `ci-hub/validate/` guards. **None of them is in the landed corpus.**
This sweep is the landed corpus; together the two cover 94 items. The 7
boolean-blind fixtures being fixed separately (`w7`) are likewise not in this
75 — but this sweep finds the same defect class at **4× the population** (31),
which is the reason to report it here.

## Method

The oracle is taken from the manifest rather than assumed. Every
`backend-parity-c` entry declares `observation = {status: true, stdout: true,
stderr: false}`, so a planted violation counts as **caught** if it changes the
exit status **or** stdout, and **not caught** if it changes neither. A stderr
change alone does not count — nobody is looking at stderr.

Mutation operator: negate the first **contract check**. Three shapes exist in
this corpus and all three are contract checks:

| shape | consequence | visible in |
|---|---|---|
| `if (GOOD) {...} else { fprintf; return 1; }` | hard failure | exit status |
| `fail("...")` helper | hard failure | exit status |
| `if (GOOD) ok++;` (always `return 0`) | count only | **stdout only** |

The third is the majority. An earlier iteration of this harness treated only
the first as a check and reported most of the corpus as having no oracle at
all — which was false, and is recorded here because it is the easy mistake.

Fixtures are run **under hermit** (`hermit run --backend ptrace`), not natively.
They pin hermit-*virtualized* values — `cpuid_probe` asserts the fabricated
CPUID identity, which no bare host satisfies — so judging them by a native run
reports working fixtures as broken. The native result is recorded for contrast
only.

**Nothing in the repository was modified**: sources are copied out and the
copies are mutated.

## Population controls — the sweep must be able to say CANNOT-FAIL

A sweep that only ever reports CAN-FAIL is the same defect it audits, so both
directions are planted and checked:

| control | mutation | verdict |
|---|---|---|
| sound fixture (check reaches `ok` and the exit code) | negate `p>0` | `ok=2`→`ok=1`, rc 0→1 ⇒ **CAN-FAIL** |
| vacuous fixture (check runs, result reaches neither stdout nor rc) | negate `p>0` | unchanged ⇒ **CANNOT-FAIL** |

So the 0 CANNOT-FAIL result below is a measurement, not an inability to detect.

## Results — 75 fixtures

| verdict | count |
|---|---:|
| **CAN-FAIL** | **71** |
| — caught by exit status *and* stdout | 40 |
| — caught by **stdout only** (exit status never moves) | **31** |
| **OBSERVATION-ONLY** (no internal check at all) | **4** |
| **CANNOT-FAIL** | **0** |

Per-fixture rows in `results.csv`.

`OBSERVATION-ONLY`: `kcmp_refusal`, `no_new_privs_refusal`, `openat2_refusal`,
`pid_probe`. These emit an observation and always exit 0. They are not broken —
their entire value is cross-backend stdout comparison — but they have no
standalone oracle, so they are worth nothing if the parity comparison is not
wired.

## Interpretation

**The fixtures can fail. Nothing runs them.**

- **0 of 78 manifest entries have any `ci=true` mode.** The entire landed
  backend-parity C corpus is unreachable by CI. This independently reconfirms
  the same conclusion reached against today's *new* fixtures, now against the
  *landed* corpus: the problem is reachability, not fixture quality.
- **6 manifest entries name a fixture that does not exist on disk**:
  `getcpu_identity`, `getpriority_identity`, `numa_node_identity`,
  `prctl_identity`, `rlimit_identity`, `sched_getaffinity_identity`. These cells
  could only ever error at compile time. Nobody notices, because `ci=false`.
- **3 fixtures on disk are named by no manifest entry**: `sigaction_state`,
  `sigaltstack_state`, `sigprocmask_state` — unreachable in the strongest sense.
- **31 fixtures move only stdout, never the exit status.** For a parity oracle
  that compares stdout this is still a live signal, so they are *not* vacuous.
  But they collapse the observation to a count: two backends observing
  **different** values that both satisfy the internal checks print the identical
  `ok=N` and pass. So they are sound for "did the contract hold" and blind to
  "did the backends observe the same value" — which is the contract a parity
  fixture claims. This is the boolean-blind class; it is 31 here, not 7.

## Reproduction

```
cd /home/newton/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
python3 experiments/fixture-can-fail-sweep_20260806/can-fail-sweep.py
```

Writes `results.json`; ~4 minutes for 75 fixtures × (clean + mutant) under
hermit. Requires `hermit/target/debug/hermit`.
