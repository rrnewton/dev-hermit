# Beyond ptrace-agreement: designing a correctness oracle for the backend parity scorecard

**Task:** `parity-against-ptrace-cannot-detect-a-shared-bug-needs-a-correctness-oracle` (P0, phase 2)
**Date:** 2026-08-05
**Scope:** local design and analysis. No `validate` run, no egress, no product change.
**Companion:** `ai_docs/verify-strip-site-audit-20260805.md` (phase 1 — what the comparison
strips). This document is about what the comparison *cannot see even when it strips nothing*.

---

## 1. The claim, and why it is sharper here than generic equivalence testing

The task states the general form: an equivalence test is blind to every shared bug by
construction. True of any differential test. But in *this* system the blindness is far
worse than the generic case, for a reason that is architectural rather than incidental:

> **The five compared backends are not five independent implementations. They all load
> the same Detcore.**

`hermit/AGENTS.md` makes this the *definition* of a backend: "a complete execution path
that loads the shared Detcore tool through Reverie as `Detcore<XxxGuest>` … Every real
backend runs the same copy of the Detcore determinism code; a separate reimplementation
or a command that merely launches a program is not a backend." Confirmed in source —
`hermit-cli/src/lib.rs:1394` (`Detcore<DbiGuest>`), `backends.rs:364-432` ("Runs
`program` through DynamoRIO with the real Detcore Tool"), and per-backend `Guest` impls
in `reverie-{dbi,kvm,sabre,liteinst,e9patch}`.

What actually differs between the compared cells is the **Guest layer**: how the process
is instrumented, how syscalls are intercepted, how registers and memory are reached. What
does *not* differ is **the determinism engine** — the scheduler, virtual time, RNG
substitution, CPUID virtualization, syscall determinization, and `/proc` synthesis.

The consequence is precise and, I think, underappreciated:

| Defect lives in | Independent evidence from a 5-backend parity comparison |
| --- | --- |
| the Guest layer (interception, register/memory access, injection) | **real** — this is what parity measures |
| Detcore (scheduling, virtual time, determinization, `/proc` synthesis) | **none, by construction** |

A Detcore-level bug is shared by all five backends *necessarily*, not by coincidence. So
the scorecard's 669 stdout-equality wins carry approximately **zero** information about
whether Hermit's determinization is *correct*. They measure backend plumbing parity. That
is a worthwhile thing to measure — it is just not the thing the number is read as.

This compounds with the phase-1 findings: the metric is **triply** blind — blind to
shared bugs (equivalence), blind to everything but stdout (projection), and blind to
vacuity (no negative side). Fixing the comparison to be bitwise addresses the second;
nothing about it addresses the first.

---

## 2. Measured: how many oracles actually exist today

I inventoried the 214-row C corpus (`compat-envelope/corpus/corpus-c.tsv`), resolving each
fixture's source in `hermit/`. 184 resolved; 30 paths did not resolve from the manifest.

Classification of the resolved 184 by what the fixture can assert *on its own*:

| Tier | Meaning | Count |
| --- | --- | --- |
| **B1** failure path + comparison to a ≥3-digit numeric literal | absolute-assertion **shaped** | 15 (8.2%) |
| **B2** failure path + `strcmp`/`strncmp` only | string comparison, needs review | 23 (12.5%) |
| **A** failure path only (exits nonzero on *something*) | liveness/crash oracle only | 118 (64.1%) |
| none | no self-check at all | 28 (15.2%) |

**Reviewing all 15 B1 entries individually, only 3 are genuine determinization oracles** —
the three already registered in `compat-envelope/absolute-oracles.csv`:

```
bin-c/robust-futex-test          [1000000]      iteration count
c-programs/io-uring-ring-determinism [4096]     buffer size
c-programs/meminfo-available-deterministic [976562]  <-- ORACLE (virtualized MemTotal)
c-programs/meminfo-cached-deterministic    [976562]  <-- ORACLE
c-programs/meminfo-free-deterministic      [976562]  <-- ORACLE
c-programs/memorypress           [10000]        iteration count
c-programs/record-replay-file-state [0200]      file mode (semantic, see below)
c-programs/resource-determinism  [2048]         buffer size
c-programs/signal-determinism    [100000]       iteration count
c-programs/syscall-file-io       [4096, 8192]   read sizes
c-programs/syscall-quick-wins    [100]          iteration count
c-programs/sysinfo-uptime        [1000, 100000000]  see below — a DIFFERENT oracle class
c-programs/thread-sync-determinism [0x1234]     sentinel value
shared-futex-c/qemu-net-init     [255]          exit-code space
util-c/pmu-skid                  [100]          iteration count
```

**This is the opposite of the hopeful reading.** The oracle registry is not
under-populated relative to the corpus; the *corpus* is. `absolute-oracles.csv` holds 3
entries and the corpus contains essentially 3 harvestable oracles. **Oracle coverage
requires authoring new assertions, not registering existing ones.** That is the real cost
and it should be planned as such.

### A refinement the inventory forced

`c-programs/sysinfo-uptime` is not an absolute-constant assertion, but it *is* a real
oracle. Its source comment: *"if scheduler is not handling uptime properly via global
clock, `uptime_1` and `uptime_2` won't be properly ordered."* It asserts an **ordering
invariant over Hermit's virtual clock** that fails independently of what any other
backend does, and it would catch a Detcore bug shared by all five.

So the task's rule — "pair equivalence with an **absolute** assertion" — is slightly too
narrow. The correct category is an **implementation-independent assertion**, of which
absolute-constant is only one species. Ordering/relational invariants, self-validating
computations, and environment-perturbation differentials are all implementation-
independent without asserting a fixed constant. The taxonomy below uses the wider
category; the scorecard schema should record *which class*, not just a boolean.

---

## 3. Oracle taxonomy

An oracle qualifies if its verdict **does not depend on the output of any other Hermit
execution**. Six classes, ordered by cost:

**O1 — Determinization constant.** Assert the guest observes the *specified virtual*
value, not the host's. `MemTotal == 976562`. Also available and unwritten: the virtual
epoch start, the determinized PID sequence, the `getrandom`/RDRAND stream prefix, the
virtualized CPUID leaf set, `sysinfo` fields, `/proc` synthesis. **Cheapest and
strongest per line of code**, because a missing-virtualization bug yields the *host*
value, which the constant rejects — in every backend at once.
*In-tree:* 3 (the meminfo family). *Negative control:* run natively, observe the host
value, confirm the assertion fires.

**O2 — Environment-perturbation differential.** Run the same backend twice while changing
something that must not matter: host PID, ASLR layout, cwd, hostname, host clock, host
load, visible RAM, CPU count, environment ordering. Any output change is a virtualization
hole. **This is not an equivalence test against another implementation** — it is a
differential against the *environment*, so it catches shared bugs.
*In-tree:* none. See §5 — this is the cheapest high-value addition available.

**O3 — Native-execution differential.** Compare Hermit's guest output against the guest
run natively, with the determinized fields excluded by a *declared* projection. Ground
truth for Linux semantics; catches "both backends mis-emulate this syscall". Constrained
to guests whose native output is itself deterministic, and it reintroduces a projection
(which must be declared and carried, per the phase-1 rules).
*In-tree:* none in the envelope.

**O4 — Self-validating guest.** The guest computes something with a known-correct answer
and exits nonzero if wrong: KAT vectors, a checksum against a hardcoded digest, "assert
this output is sorted". Fully independent of Hermit; the assertion travels with the
guest. This is the natural home for most new authoring, and it is what the 118 tier-A
fixtures *almost* are — they exit nonzero on a crash, but assert nothing about the answer.
*In-tree:* partially, in the e2e real-execution corpus; not wired into the envelope.

**O5 — Relational / metamorphic invariant.** A property that must hold between related
runs without knowing the right answer: record-then-replay must reproduce the recording;
N-thread and 1-thread runs under sequentialization must agree; virtual clock readings must
be ordered consistently with the schedule.
*In-tree:* `sysinfo-uptime` (clock ordering) and the whole record/replay path — which is
already a metamorphic oracle and should be *counted* as one.

**O6 — Independent reference implementation.** Differential against a non-Hermit
deterministic executor (rr, dettrace, gVisor, QEMU). Genuinely independent, expensive,
and each reference has its own bugs. Reserve for a small high-value subset.
*In-tree:* the gVisor comparison is referenced in policy for KVM work but is not an
envelope oracle.

---

## 4. The analytic core — which oracle catches which shared bug

A taxonomy without this mapping is just a list. Shared-bug classes are the rows; an oracle
class is useful for a cell only if it can *fail* when that bug is present.

| Shared bug | ptrace parity | O1 const | O2 env-perturb | O3 native | O4 self-valid | O5 relational |
| --- | --- | --- | --- | --- | --- | --- |
| **S1** source not virtualized at all (host value leaks identically) | blind | **catches** | **catches** | catches | — | — |
| **S2** virtualized to a *wrong but stable* value | blind | **catches** (if spec pins it) | blind (it is stable) | **catches** (if guest-visible) | catches | — |
| **S3** shared Detcore semantic bug (syscall mis-emulated) | **blind by construction** | — | — | **catches** | **catches** | — |
| **S4** shared scheduling bug (deterministic but wrong order) | blind | — | — | partial | catches | **catches** |
| **S5** vacuity (guest does nothing observable) | blind | **catches** (assertion must run) | — | catches | **catches** | — |
| backend plumbing divergence | **catches** | — | — | — | — | — |

Three things fall out:

1. **Parity's only column is the last row.** It is the right tool for exactly one job.
2. **S3 has no cheap oracle.** The most dangerous class — a Detcore bug shared by
   construction across all five backends — is reachable only by O3 (native differential)
   or O4 (self-validating guests), the two most expensive classes. This is where the
   investment has to go, and it explains why the envelope currently has no defence
   against it at all.
3. **O1 and O2 are complementary, not redundant.** O1 catches wrong-but-stable (S2) and
   O2 does not; O2 catches leaks O1 has no constant for. Together they cover S1 fully at
   low cost.

---

## 5. The cheapest high-value addition: perturbed `--verify`

`--verify` today runs the guest twice **under identical conditions** and compares. By
construction that can only detect *spontaneous* nondeterminism — a race, an uninitialized
read, a a host-timing-dependent path. It cannot detect a systematic host-value leak,
because the leaked value is the same in both runs.

Change what varies between the two runs and the *same machinery* becomes an O2 oracle:

- run 2 under a different host PID / PID-namespace offset
- run 2 with a different ASLR layout (`setarch -R` off/on)
- run 2 from a different cwd, hostname, or `TZ`
- run 2 with the host clock stepped
- run 2 with a different visible CPU count or cgroup memory limit

Any output difference is a virtualization hole, detected **without reference to another
backend**. The cost is low: the run-twice-and-compare harness, the comparison policy
(`Canonical`), and the verdict plumbing (`ComparisonSpec`, `bitwise_parity`) all already
exist from #1595. What is missing is a knob that perturbs the second run and records
*which* perturbation was applied in the verdict.

**Design constraint, learned from phase 1:** the perturbation must be recorded *with* the
verdict, exactly as the canonicalization policy is. A green from a perturbed verify means
something strictly stronger than a green from an identical-conditions verify, and a
consumer must be able to observe which one it holds — not infer it. Add
`perturbation: {kind, applied}` to the verdict; absent means unperturbed, never
"unknown-so-assume-strong".

**Expected consequence, stated in advance:** turning this on will produce *new reds* on
cells that pass today. Those reds are genuine findings — real virtualization holes that
identical-conditions verify was structurally unable to see. Per the standing rule, each
gets filed; none is masked by widening the comparison back.

---

## 6. What a scorecard cell must carry

Phase 1 established the cell needs two numbers, not one. This phase says the second
number needs a *type*:

```
cell = {
  equivalence: { contract: BitwiseInfoV1, result, receipt },   # matches ptrace
  oracle:      [ { class: O1|O2|O3|O4|O5|O6,
                   id, source_path, source_sha256,
                   negative_control_path, negative_control_sha256,
                   result } ],                                  # is CORRECT
}
```

Rules, all inherited from the phase-1 proxy-binding discipline:

- **A cell with no oracle entry is UNQUALIFIED, not green.** Missing evidence renders
  unqualified; it never renders as a pass.
- **An oracle without a dereferenced negative control does not count.** The planted
  violation must be shown to make the assertion fire. This is already the shape of
  `absolute-oracles.csv` and should not be weakened.
- **Record the class.** "Has an oracle" is not enough: an O1 constant and an O5 relational
  invariant defend against different bugs (§4), so a coverage claim must be per-class or
  it cannot be checked against the bug matrix.
- **Do not let the oracle count be inferred from the equivalence result.** They are
  independent axes; a cell can be equivalence-green and oracle-red (both backends wrong
  the same way — the entire point of this task) or equivalence-red and oracle-green
  (backend plumbing differs, both compute correctly).

---

## 7. Costed adoption plan

Ordered by value per unit of work. Steps 1–2 need no new test authoring.

1. **Count what already exists, correctly.** Register `sysinfo-uptime` (O5) and audit the
   record/replay path as the O5 oracle it already is. Re-render the scorecard with a
   per-class oracle column. Cost: hours. Moves the honest oracle count off 3.
2. **Add perturbed `--verify` (O2).** One flag, one verdict field, reusing the #1595
   machinery (§5). Cost: small. Coverage: S1 across *every* cell at once — the only
   proposal here with corpus-wide reach for a fixed cost.
3. **Author O1 constants for the determinization surface.** Virtual epoch, PID sequence,
   RNG stream, CPUID leaves, `sysinfo`, `/proc` synthesis. Each is a small C fixture in
   the meminfo mould plus a host-negative control. Cost: ~1 fixture per determinized
   source. Coverage: S1+S2 on the sources that matter most.
4. **Convert tier-A fixtures to O4.** 118 fixtures already have a failure path but assert
   nothing about the answer. Adding a known-answer check to the highest-value ones is the
   main lever on **S3**, the class with no cheap defence. Cost: real, per-fixture, and it
   should be prioritized by which Detcore behaviour the guest exercises rather than by
   what is easy to assert.
5. **O3 native differential for a chosen subset.** Highest S3 yield per test, but needs
   the declared-projection machinery to exclude determinized fields. Sequence after the
   phase-1 comparison contract is settled.
6. **O6 reference differential.** Defer. Small subset only, once 1–5 are in place.

---

## 8. What this document does not establish

- **No measurement of how many current parity wins hide a shared bug.** That number is
  unobtainable by construction — if we could see the shared bugs we would fix them. The
  argument here is structural (what the comparison *can* detect), not an estimate of
  defects present.
- **The corpus inventory is a heuristic classification**, run over the 184 resolvable C
  fixtures of the 214-row manifest; 30 manifest paths did not resolve and were not
  reviewed. Tier assignment came from regex plus my individual review of the 15 B1
  entries; the 23 B2 (`strcmp`) entries were **not** individually reviewed and may contain
  further genuine oracles. The 21 non-C corpus rows were not inventoried.
- **No oracle was implemented or run.** This is a design pass; nothing here has been
  bracketed against a live execution.
- The `sysinfo-uptime` reading comes from its source comment and structure, not from
  observing it fail — its status as a working O5 oracle should be confirmed by planting a
  clock-ordering violation before it is registered.

## Reproduction

```bash
cd ~/work/dev-hermit
# corpus oracle inventory (tiers B1/B2/A/none)
python3 - <<'PY'
import re,pathlib
H=pathlib.Path('hermit')
rows=[l.split('|') for l in open('compat-envelope/corpus/corpus-c.tsv') if l.strip()]
fail=re.compile(r'\bassert\s*\(|\breturn\s+[1-9]|exit\s*\(\s*[1-9]|EXIT_FAILURE|"FAIL')
num=re.compile(r'(?:==|!=|<|>)\s*(0x[0-9A-Fa-f]{3,}|\d{3,})\b')
for tid,src,*_ in rows:
    p=H/src
    if p.exists():
        t=p.read_text(errors='replace')
        if fail.search(t) and num.search(t):
            print(f"{tid:52s} {sorted(set(num.findall(t)))}")
PY
# shared-Detcore confirmation
grep -rn "Detcore<" --include=*.rs hermit/hermit-cli/src/
```
