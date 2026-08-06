# Resource accounting: deterministic, but CPU time is zeroed — and the virtual clock contradicts it

**Task:** `rusage-resource-accounting-determinism` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Answer

**Determinism: PASS.** Every field is byte-identical across separate invocations on ptrace, DBI and
SaBRe, while the native control varies — so the stability is earned. **No host value leaks.**

**#140: FAIL, on the exact anti-pattern the task names.** `getrusage`'s CPU and fault counters are
**zeroed**, not virtualized. And the run's own virtual clock proves the information exists to do
better: **`/proc/uptime` advances 123.00 → 130.00 in the same run where `utime` stays `0`.**

## Measured

Guest samples `getrusage`, `times` and `getrlimit` **before and after** a 30 000 000-iteration
floating-point loop (0.12 s of real CPU natively).

| field | ptrace | dbi | sabre | verdict |
| --- | --- | --- | --- | --- |
| cross-invocation | IDENTICAL | IDENTICAL | IDENTICAL | **PASS** (native varies) |
| `rlimit NOFILE` | 1048576 | 1048576 | 1048576 | **PASS** — fixed, consistent, and ≠ native (524288) |
| `ru_maxrss` before→after | 1564 → **1692** | 10628 → **10776** | 38068 → **38144** | **PASS — real, evolving, deterministic** |
| `ru_utime` | 0.000000 → **0.000000** | same | same | **#140 FAIL — zeroed** (native: 0 → 0.122762) |
| `ru_stime` | 0.000000 | 0.000000 | 0.000000 | #140 FAIL — zeroed |
| `ru_minflt` | 0 | 0 | 0 | #140 FAIL — zeroed (native 101 → 103) |
| `ru_majflt`/`nvcsw`/`nivcsw` | 0 | 0 | 0 | #140 FAIL — zeroed (native `nvcsw`=1) |
| `times()` return | 12045 → 12045 | 12030 → 12030 | 12002 → 12002 | frozen in-run, **backend-dependent** across |
| `times()` `tms_stime` | **44 → 45** | 29 → 29 | 2 → 2 | **inconsistent — advances on ptrace only** |

### `maxrss` is the counter-example that makes this actionable

`ru_maxrss` is deterministic **and** it evolves **and** it differs sensibly per backend (DBI and SaBRe
carry more resident memory than ptrace, as you'd expect). So the "deterministic *and* continuously
evolving" property is already achieved for one field in the same struct. The zeroed fields are not
zeroed because determinism demanded it.

### The sharpest evidence: the virtual clock contradicts the accounting

One run, ptrace, reading both:

```
uptime-1: 123.00 0.00      times-1 : 0 0 0 0
  ... 300 000-iteration shell loop ...
uptime-2: 130.00 0.00      times-2 : 0 0 0 0
```

**Seven virtual seconds elapse and the process's own CPU accounting records none of it.** A guest
computing `utilization = utime / elapsed` gets `0 / 7` — deterministically, confidently wrong. This is
not "unimplemented"; it is two views of one virtual timeline disagreeing, and the timeline that
advances is already there to derive from.

## Recommendation, honoring #140

The task warns against the "return 3" anti-pattern, and that is precisely what `utime = 0` is.

1. **Derive `ru_utime`/`ru_stime` from the existing virtual-time accounting**, the same source
   `/proc/uptime` already reads. It must stay **continuous and fine-grained** — a value that advances
   with executed work, not a constant and not a coarse tick.
2. **Make `/proc/self/stat` fields 14–17 and `times()` read from that same source**, so the three
   views cannot disagree. Today they can, and do.
3. **Fault and context-switch counters** (`minflt`, `majflt`, `nvcsw`, `nivcsw`) should be derived from
   events detcore already mediates — page faults and scheduling decisions are exactly what it sees.
   Zero is a wrong answer that happens to be stable.
4. **Reconcile `times()` across backends**: its return differs per backend (12045/12030/12002) and
   `tms_stime` advances on ptrace only. Whatever the source, it is not shared.
5. **Use `ru_maxrss` as the template** — it is already right.

## Scope and limits

* **KVM not tested** (livelocks at guest startup on this host).
* One guest, single-threaded; N=2 invocations per backend.
* I did **not** determine where the zeroing is implemented, nor whether it is deliberate policy or
  unimplemented accounting. Both are consistent with the evidence; the fix direction is the same.
* No code changed.

## Provenance (#268)

`worktrees/oci/hermit/target/release/hermit`, built 2026-08-06 04:30, `--features
third-party-backends`. Guest `~/.local/hermit-deps/guests/guest_rusage` (`gcc -O1`; source committed as
`guest_rusage.c`). Flags `--strict --no-virtualize-cpuid --max-timeslice=disabled`.
`LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64`. Raw outputs committed as `out-{ptrace,dbi,sabre}.txt`.
