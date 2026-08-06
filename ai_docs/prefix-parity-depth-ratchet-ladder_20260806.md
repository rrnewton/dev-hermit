# Prefix-parity depth Y/Z — the ratchet is pinned at the process prologue, not at the workload

**Task:** `demo5-prefix-parity-depth-ratchet` (owner's metric #315/#316) · hermit-w2
(`[impl agent, opus-5]`) · **2026-08-06** · local, no egress.

**Anchor.** `ignored/det4-parity/hermit/target/release/hermit`, self-reported
`hermit 0.2.0 (2026-08-06, g4c70658e7858)` — **clean**, no `-dirty` marker; checkout clean at
`4c70658e785834737cbe1524f77330c781a6f5ea`, reverie pin `dd3c178`, release,
`--features third-party-backends`. Host devbig014.

## Definition (#316 — recorded, because the number is only monotonic under a fixed one)

* **record** = a log line containing `DETLOG` or `COMMIT turn`
* **Z** = records in the **ptrace golden** for that rung
* **Y** = length of the longest common **prefix** of records, backend vs golden
* **normalization** = the real wall-clock prefix **only**. Syscall values, counts, flags, addresses
  and virtual time compared verbatim. **Full INFO log, not stdout.**

Unchanged from `ai_docs/prefix-parity-depth-remeasured_20260806.md`, so these numbers are directly
comparable to the previously published `sabre 0/Z, dbi 3/Z`.

**Precondition, enforced per rung:** the golden is captured twice and must be bit-identical, else
the rung is DISQUALIFIED rather than measured. All eight rungs below passed.

## The table

| rung | Z (golden) | ptrace (sanity) | **dbi** | **sabre** | **liteinst** | e9patch | kvm |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `/bin/true` | 145 | 145/145 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |
| `/bin/echo hi` | 336 | 336/336 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |
| `/bin/cat /etc/hostname` | 353 | 353/353 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |
| `/bin/wc -c /etc/hostname` | 343 | 343/343 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |
| `sh -c 'echo a \| wc -c'` | 1,297 | 1297/1297 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |
| `python3 -c 'print(1)'` | 2,833 | 2833/2833 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |
| `dd bs=1 count=10000` | 40,931 | 40931/40931 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |
| `dd bs=1 count=100000` | 400,940 | 400940/400940 | **3** | **1** | **8** | NOT-ENGAGED | TOOL-ERROR |

`ptrace` is the golden double-run sanity row and is Z/Z everywhere, as the definition requires.

## The finding: every depth is CONSTANT across four orders of magnitude of Z

Z ranges from 145 to 400,940 — a factor of **2,765** — and **not one backend's depth moves**. dbi is
3 at every rung, sabre 1, liteinst 8.

**The ratchet is not currently measuring workload parity. It is measuring a process-prologue
divergence.** Every backend diverges in the first handful of records, during process setup, before
the guest does any of the work the rung was chosen to exercise.

Three consequences, and they change what to do next:

1. **Adding heavier rungs cannot move these numbers.** Nor could fixing demo05. A bigger denominator
   with the same tiny numerator only makes the *percentage* look worse while nothing has changed.
2. **The ratchet's own loop (#315) — "first diverging commit → that's the next thing to unblock" —
   points at exactly one place for each backend, and it is the same place at every rung.**
3. **A percentage is the wrong headline here.** `3/400940` and `3/145` are the same fact.

## First divergence, per backend — the actual unblock list

### dbi (the leading non-ptrace backend) — depth 3, and 114 of 118 records differ

The first differing record is `COMMIT turn 0`, and the difference is that dbi renders the **raw host
pid** where the golden has the determinized `DetPid(3)`:

```
golden :  COMMIT turn 0, dettid 3      … {ParentContinue { parent: DetPid(3),      child: DetPid(3) }: W}
dbi    :  COMMIT turn 0, dettid 534947 … {ParentContinue { parent: DetPid(534947), child: DetPid(534947) }: W}
```

That number changes every run (534947, 567103, 616077, …), so **dbi's DETLOG is not even
self-deterministic** in these fields.

Peeling the causes one at a time (**diagnostic folds — NOT the ratchet number, which stays 3**), on
`/bin/true`, Z=118:

| fold applied | depth | records still differing |
| --- | ---: | ---: |
| none (strict) | 3 | 114 / 118 |
| + all pid forms (`dettid`, `DetPid(`, `dtid`) | 7 | 103 |
| + syscall ordinal `#N` | 7 | 101 |
| + guest addresses | **15** | **16** |

So **the dominant blocker is address-space layout, not pid rendering**: folding addresses collapses
the differing-record count from 114 to 16. The concrete divergence:

```
golden : finish syscall #2: brk(NULL) = Ok(93824992260096)      # 0x5555_5555_6000-ish, PIE region
dbi    : finish syscall #1: brk(NULL) = Ok(140737282834432)     # 0x7fff_f7xx_xxxx
```

The guest's heap base lands in a completely different region under DynamoRIO, and the stack pointer
shifts with it (`arch_prctl(12289, 0x7fffffffb2e0)` vs `0x7fffffffa9f0`). Note also the syscall
ordinal is off by one — dbi counts one fewer syscall before `brk`.

**Unblock order for dbi: (1) virtualize the pid in the DETLOG, (2) reconcile guest address-space
layout, (3) reconcile syscall accounting.** After (1) and (2) only 16 of 118 records still differ.

Encouraging context: dbi emits **the same record count as the golden** at the small rungs
(145 vs 145, 353 vs 353) and stays within ~1% at the large ones (403,338 vs 400,940), so its
*execution structure* already tracks the golden closely. The 3 is rendering and layout, not control flow.

### sabre — depth 1, and the trace is truncated, not merely divergent

First divergence is record 1: the golden emits
`DETLOG USER RAND: seeding PRNG for root thread with seed 0` and sabre does not — it goes straight to
`COMMIT turn 0`. That matches the "log plumbing" characterisation in the prior artifact.

But the more important number is the **record count**: sabre emits 4 / 33 / 34 / 34 / 116 / 136 /
83 / 91 records against goldens of 145 … 400,940. At `dd-100k` sabre produces **91 records where the
golden has 400,940**. Sabre is not producing a comparable trace at all, so `1/Z` understates the
situation — it is closer to "no trace to compare" than to "diverges at record 1". Its runs report
`SaBRe ptrace fallback completed ptrace_fallback_sites=0 trusted_shared_object_sites=0`.

### liteinst — depth 8, address-space layout again

First divergence is the same class as dbi's second blocker — a guest stack address:

```
golden   : inbound syscall: arch_prctl(12289, 0x7fffffffb260)
liteinst : inbound syscall: arch_prctl(12289, 0x7fffffffb1d0)
```

liteinst emits **more** records than the golden at small rungs (937 vs 145) and **far fewer** at
large ones (1,382 vs 400,940), so its trace diverges structurally as the workload grows.
Engagement is genuine and verified: `activation verified (traps=1, hooks=31)`.

## e9patch: a perfect score that must never enter the ratchet

`--backend=e9patch` reports, on every rung:

```
:: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=0; mapped_sites=0; b0_sites=0
```

It patched **nothing** and ran the ordinary ptrace runtime — so it produces a byte-identical log and
**would have scored a flawless `Z/Z`, up to `400940/400940`, while instrumenting zero code.**

This is the proxy-binding failure in its purest form: `--backend=e9patch` is a *label*;
`mapped_sites=0` is the *observable* that refutes it. The harness therefore asserts engagement from
each run's own output and reports **NOT-ENGAGED**, never a parity score. Any ratchet consumer that
keys on the backend flag alone will silently record e9patch at 100%.

`kvm` is TOOL-ERROR (rc=124, wall-clock timeout) on this host — the known `--strict` hang; it is
reported as TOOL-ERROR and never as a depth.

## Two harness traps worth not re-hitting

* **dbi does not honour `--log-file`.** No file is created; every DETLOG goes to **stderr**, because
  detcore runs inside a DynamoRIO client. Reading only the log file scores dbi **0 records** and
  looks like a crash. Records are taken from the log file when non-empty, else stderr.
* **Engagement evidence lands in different streams per backend** — sabre writes `hermit::sabre` to
  the *log file* while dbi/e9patch/liteinst announce themselves on *stderr*. Searching only one
  stream misreported sabre as NOT-ENGAGED in an earlier pass of this work.

## demo05

Not measured here, and deliberately. Its golden fails the self-determinism precondition: see
`ai_docs/demo05-golden-capture-fixed-and-residual-disqualification_20260806.md` — five distinct
qcow2 SHAs in ~18 controlled runs, caused by PMU `rcbs ±1` drift rather than capture hygiene, and
disqualified *after* the capture was fixed. Reporting a backend depth against a reference that is
not self-identical would attribute to the backend a divergence no backend fix can close.

The `dd bs=1 count=N` rungs are its stand-in: qualified, QEMU-free, and tunable to any record count
(400,940 records here; 1.6 M measured in the prior task). **The ratchet does not need demo05** —
and, per the constancy finding above, would learn nothing new from it at present.

## Limitations

* One run per (rung, backend) for the backend arm; the *golden* is double-run checked per rung, but
  a backend depth of 3 is not itself replicated n=3. Given every depth is constant across eight
  rungs, replication is cheap insurance rather than a live doubt.
* The fold table is `/bin/true` only; I did not repeat the layered decomposition at other rungs.
* Depth counts records, not "commits" specifically. `COMMIT turn` lines are a subset of records;
  under a commits-only definition the numbers would differ and would need re-baselining (#316).
* `kvm` was confirmed TOOL-ERROR once and then excluded from the per-rung loop to avoid spending a
  timeout per rung; the table reports that uniformly rather than re-measuring it eight times.
* e9patch was not investigated beyond `mapped_sites=0` — whether it *can* patch these guests, and
  under what configuration, is untested.

## Reproduction

```sh
TMO=600 python3 experiments/prefix-parity-ratchet_20260806/ratchet.py
# single rung / subset:  ONLY=true BACKENDS=dbi,sabre python3 …/ratchet.py
```
