# Strict corpus: first JIT runtime, first real multithreaded application — and an L3 gap

**Task:** `expand-strict-corpus-new-e2e`
**Date:** 2026-08-06
**Agent:** `egress-probe2` (opus-5)
**Change:** hermit branch `feat/e2e-jit-and-thread-corpus` @
`0cab21576285d05248b1dd561151591acaf223d6`, slot `worktrees/strictcorpus/hermit`,
base = parent-pinned hermit HEAD. **Not pushed** — GitHub egress is 403.
**Follow-up filed:** `l3_stack_content_divergence` [P2].

---

## 1. What was added, and why each is genuinely new surface

The bar the task set is "real syscall/JIT/runtime surface, NOT `--version`
probes". Both cases were chosen by asking what mechanism the existing 50-entry
corpus never touches.

### `language-runtimes/node-v8-jit` — the corpus's first JIT-compiling runtime

Every language fixture already present (Lua, Perl, Python, Ruby, Tcl, gawk, m4,
bash) is a bytecode or tree-walking interpreter: **none writes machine code at
run time**. V8 does. The hot loop drives Ignition → Sparkplug → TurboFan
tiering, which allocates writable-then-executable code pages (mmap/mprotect with
`PROT_EXEC`, W^X flips) and installs optimized code mid-run, alongside V8's
background compiler and GC threads.

The fixture asserts **two independent things**, on purpose:

| output | role |
| --- | --- |
| `random` | V8's xorshift128+, seeded from `getrandom(2)`. Varies natively; must repeat under `--strict`. This is the determinism claim. |
| `jit-checksum` | An integer reduction, deterministic by construction. This is a **correctness oracle for the JIT under interception**. |

The second one matters more than it looks: a determinism-only assertion cannot
distinguish "optimized code ran correctly" from "the JIT silently never
engaged". `--jitless` is deliberately not passed — disabling the JIT would
remove the surface the fixture exists to cover.

### `data-handling/zstd-multithread` — a real multithreaded application

The corpus has multithreaded workloads, but all are purpose-built C sentinels
under `determinism-stress`. `zstd -T4` is a real application: a worker pool over
a futex-coordinated job queue that reassembles a frame in order. The fixture
asserts the **assembled artifact is byte-identical**, which a thread test that
only checks completion cannot.

Two deliberate choices, both recorded in the fixture:

* **`-T4`, not `-T0`.** `-T0` binds the worker count to the host core count,
  which differs between this 316-core dev box and a 4-core hosted runner, and
  the job split — hence the frame — depends on it. Pinning it keeps the digest a
  property of the code under test, not of the machine.
* **`naked` mode disabled, with the reason in the manifest.** zstd fixes job
  boundaries by input offset, not by which worker claims a job, so the frame is
  deterministic natively. This is a *mechanism* test, not a native-variance
  test, and saying so beats asserting a variance that does not exist.

## 2. Evidence

Backend `ptrace`, `--log INFO`, relaxations **none**.

| fixture | corpus L2 gate (`--strict --verify`) | DETLOG messages compared |
| --- | --- | --- |
| `node-v8-jit` | **PASS** | 288 325 \| 288 325 |
| `zstd-multithread` | **PASS** | 25 731 \| 25 731 |

Both also **PASS through `ci/test_harness.sh run --test`** on the portable lane —
not merely as ad-hoc `hermit run` invocations, which would not prove they work as
manifest cells.

**Determinism bracket** (the fixture must not be trivially constant):

```
native:            random=0.006479646373
                   random=0.038575976335   → three distinct
                   random=0.680810516423
hermit --strict:   random=0.736159682664
                   random=0.736159682664   → three identical
                   random=0.736159682664
```

The determinized value differs from every native draw, so entropy is being
*determinized*, not passed through.

**Registration + validation.** Bucket manifests, `inventory/test-files.json`,
and `ci/expected-e2e-plan.json` (**80 → 82** required cells: exactly +2
`verify/ptrace`, zero removed). `ci/test_harness.sh validate` passes — 308 E2E
tests valid, DAG corresponds with no dangling deps. All six edits are additive
(+310/−0).

## 3. The L3 gap — the part worth acting on

The task asked for "full detlog-stack/heap/INFO parity vs ptrace, not just
stdout/exit". The corpus gate already does INFO parity (that is what the 288 325
compared DETLOG messages are). Pushing to L3 found a real hole.

`zstd-multithread` **passes L2 and fails L3** (`--detlog-heap --detlog-stack`,
rc=1, "Failure: nondeterministic"):

```
INFO detcore: DETLOG [memory][dtid 3] 0x7ffffffdc000-0x7ffffffff000 \
  MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]-><DIGEST>
```

Same address range, same permissions, **different `[stack]` content digest**
between run 1 and run 2 (`a5a6dbae20ea7…` vs `e8380d…`), at DETLOG message 2803
(dtid 3) and again at 51581 / 51588 (dtid 63, a worker thread).

**This is a real divergence, not the known comparator artifact.** In the
`--verify-strict` FullTrace gap, identical rendered text is flagged as divergent.
Here the bytes genuinely differ.

**Control that makes it attributable:** `system-utils/shuf-permutation`
(single-threaded) **passes L3** on the same binary in the same session. So this
is neither a blanket L3 gap nor an artifact of the new fixture — L3 is a live,
**discriminating** ratchet, and this is a real hole in it.

**Hypothesis, explicitly not confirmed:** that multithreading is the *cause*
(worker-thread stack contents not fully determinized). Confirming it needs an
ST-vs-MT discriminator on the same program. I attempted `xz -T1` vs `-T4` and
could not afford it — L3 tracing under a **debug** hermit exceeded a 420 s bound
even single-threaded (rc=124), and `node-v8-jit`'s own L3 probe exceeded ten
minutes. **First step for whoever picks this up: build a release hermit.** Do not
attempt L3 discrimination at debug.

## 4. Corrections and dropped work — stated, not buried

* **I initially recorded `lua-random` as an L3 FAIL. That was wrong.** Lua is not
  installed on this host; the fixture errored with `exec: : not found` because I
  invoked `--run` directly, bypassing its `--prepare` availability gate. Not a
  determinism data point; discarded before it reached any conclusion.
* **A PHP fixture was written and then dropped.** `/usr/local/bin/php` on this
  box is **HHVM 6.79 (HipHop VM)**, not PHP. The fixture would have tested a
  different runtime than its name claimed, on a host-specific binary that would
  `SKIP` on the portable lane — an inert cell. Two solid cases beat three with
  one mislabeled.
* **An unnecessary 348-line churn was reverted.** My first inventory edit
  re-sorted the whole file. Redone as a minimal +12/−0 insertion in the file's
  existing order; the plan ratchet likewise went from a 70-line reformat to
  +14/−0.

## 5. Environment fix made en route

`systemd-tmpfiles-clean` had wiped `/tmp/lu/usr/lib64/*.so` — the fleet's
libunwind workaround — so the build failed with `unable to find library
-lunwind`. The `.pc` files survived, which is why the failure looked like a
pkg-config problem rather than a missing-file one. Re-extracted the surviving
RPMs to a **durable** prefix, `/home/newton/.local/libunwind`, and built from
there:

```bash
LU=/home/newton/.local/libunwind/usr
export PKG_CONFIG_PATH=$LU/lib64/pkgconfig LIBRARY_PATH=$LU/lib64 \
       C_INCLUDE_PATH=$LU/include LD_LIBRARY_PATH=$LU/lib64
```

Anything under `/tmp` will be cleaned again; the workaround should not live
there.

## 6. Residue

1. **Nothing pushed** (egress 403). Branch needs a PR against
   `rrnewton/hermit:main` and an exact-head validate receipt when egress returns.
2. **L3 gap open** — `l3_stack_content_divergence` [P2].
3. **Backends other than ptrace not ratcheted.** By design: this is the
   top-of-funnel step. Both fixtures record per-backend `backends_disabled`
   reasons, and the two JIT-adjacent ones are substantive rather than
   boilerplate — DBI rewrites the code stream and LiteInst patches it, so a
   runtime that writes its own code needs separate qualification.
4. **`chaos` mode disabled for `zstd-multithread`, flagged as a genuine
   follow-up** rather than a permanent no: the worker pool *is* real guest
   concurrency, but the frame digest must first be shown stable under chaos
   scheduling before it can gate CI.
