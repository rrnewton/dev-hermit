# DBI strict parity vs ptrace: the first cross-backend INFO/stack/heap measurement

**Task:** `ratchet-dbi-strict-parity` · **Date:** 2026-08-06 · Local, no egress, no validate run.

## Why this needed a new harness

`compare_two_runs` (hermit-cli) has exactly two live callers and **both are same-backend**
(run-twice, or record-vs-replay). The product can answer *"is this backend self-consistent?"*
and **cannot** answer *"does this backend match ptrace?"* — the parity axis. `compare.py`
here is that missing comparator, built outside the product so the gap can be **measured**
before it is built properly.

Method: same binary for both backends (`worktrees/covnode/hermit` @ `fc49593ac`, which has
DBI compiled in), **pinned environment** (`env -i` + 6 vars — non-negotiable; an unpinned
environment puts `INVOCATION_ID`/scope names into the guest's `envp` and therefore its
initial stack, which makes every stack hash differ under *every* backend), boxed via
`hermit-box-run`, `--strict --detlog-stack --detlog-heap --log info`.

## Headline

> **DBI INFO parity vs ptrace: 0 of 6. Stack parity: 0 of 6. Heap: no-result on all 6.**
>
> **Not one of those zeros is attributable to guest behaviour.** All of them are explained
> by three defects in DBI's *logging and determinization plumbing* — and one of the three
> is a genuine determinism bug that makes DBI log parity **impossible by construction**
> until fixed.

This is the precise distance between the reported "DBI ~85.5% (130/152)" — which is
**stdout+exit** — and strict parity. The guests all ran fine under both backends and
produced identical stdout; the parity failure is entirely in what gets logged and how.

## GAP-1 — `--log-file` is silently ignored on DBI; the log goes to stderr

Same command, same binary, same guest, only `--backend` differs:

| | ptrace | dbi |
| --- | --- | --- |
| `--log-file` honoured | **yes**, 85 149 bytes | **no — file never created** |
| stderr | 0 lines | **535 lines, 375 `DETLOG`** |
| `[memory]` records | 123, in the log file | 123, **in stderr** |

The data exists on both sides in equal quantity (123 records each) — it is purely
misrouted. Any consumer that reads the log file (including `--verify` and the
compat-envelope collector) sees **nothing** from DBI, which is why DBI has only ever been
scored on stdout.

**Precedent for the fix:** SaBRe has exactly this problem and it is already handled —
`run.rs:75-98` `extract_sabre_detlogs()` lifts SaBRe's stderr DETLOGs into the log file
before comparison. **DBI needs the same treatment** (better: both should route through the
log-file plumbing rather than each getting a bespoke rescue).

## GAP-2 — DBI log lines carry no wall-clock prefix, so detcore cannot even parse them

```
ptrace: 2026-08-06T11:25:54.226976Z  INFO detcore: DETLOG [syscall][detcore, dtid 3] inbound syscall: brk(NULL) = ?
dbi:                                 INFO detcore: DETLOG [syscall][detcore, dtid 4067319] inbound syscall: brk(NULL) = ?
```

`detcore::logdiff::extract_log_messages` **splits the stream on that timestamp** — it is
tier-1 of the canonical policy ("strip the real wall-clock prefix"). With no prefix present
the whole DBI stream collapses into **one message**, which is exactly what this harness saw
before the defect was identified (`dbi 1 msg` against `ptrace 194`). So DBI's log is not
merely misrouted, it is **not in the format detcore's own comparison machinery accepts**.

## GAP-3 — `dtid` is the raw HOST TID under DBI (determinism bug)

The sharp one. `dtid` should be the determinized thread id. Across seven runs of the same
binary:

| backend | observed `dtid` values |
| --- | --- |
| **ptrace** (control) | `3, 3, 3, 3, 3, 3, 3` — constant |
| **dbi** | `4014319, 4066600, 4067319, 4067656, 4067924, 4069129, 4071477` — **7 distinct**, tracking host PID allocation |

That is a host value leaking into the DETLOG stream. It is **not** a formatting nit:

- every DETLOG line carries a `dtid`, so **every line differs** from ptrace's, and
- it differs from **DBI's own previous run**, so DBI cannot even self-verify bitwise.

**DBI log parity is impossible by construction until this is determinized.** Fix GAP-1 and
GAP-2 and parity is still 0; fix GAP-3 and the other two become mechanical.

Related divergences visible in the same lines, not separately investigated: `brk(NULL)`
returns `Ok(140737282834432)` under DBI vs `Ok(93824992264192)` under ptrace, and syscall
numbering starts at `#1` under DBI vs `#2` under ptrace.

## Heap: no-result everywhere

`heap` was `NO-RESULT` on all 6 cells (no `[heap]` records on one or both sides). Consistent
with the separately-measured finding that the shipped heap domain is the **brk segment
only** and captures ~0.2% of a program's non-executable anonymous memory — these small
guests have essentially no brk heap to hash. **A heap-parity number cannot be produced at
all until the heap domain is fixed**; reporting "heap matches" for these cells would have
been vacuous.

## Results (6 cells)

| test | guest | info | stack | heap |
| --- | --- | --- | --- | --- |
| true | `/bin/true` | DIVERGE | DIVERGE | no-result |
| echo | `/bin/echo hello` | DIVERGE | DIVERGE | no-result |
| pwd | `/bin/pwd` | DIVERGE | DIVERGE | no-result |
| date-utc | `/bin/date -u +%Y` | DIVERGE | DIVERGE | no-result |
| head-etc-hostname | `/usr/bin/head -c 16 /etc/hostname` | DIVERGE | DIVERGE | no-result |
| wc-self | `/usr/bin/wc -c /bin/true` | DIVERGE | DIVERGE | no-result |

The portable per-cell summary is `results.csv`. Full per-cell data including message
counts, first-divergence lines, and per-side record hashes is retained in `results.json`.
Raw logs per cell are under `runs/<test>/`.

## Fix order (each is independently landable)

1. **GAP-3 first** — determinize `dtid` under DBI. Nothing else moves the parity number
   while every line carries a host TID.
2. **GAP-1** — honour `--log-file` on DBI, or lift its stderr DETLOGs the way
   `extract_sabre_detlogs` does for SaBRe. Prefer fixing the routing for both backends over
   a second bespoke rescue.
3. **GAP-2** — emit the standard wall-clock prefix so `extract_log_messages` can split the
   stream. (Largely subsumed by 2 if DBI routes through the normal log plumbing.)
4. Re-run this harness. Expect the number to move off 0 only after 1–3; then the residual
   divergences are candidates for *real* backend-parity bugs, which is where the ratchet
   actually begins.

## Limitations

- **6 trivial guests, not the 152-cell corpus.** The corpus run is the obvious next step;
  it was not run because all 6 cells fail for the same three structural reasons, so more
  cells would multiply the same finding rather than add information. The denominator here
  is 6, and I am not extrapolating it to 152.
- **No fix was implemented.** All three gaps are hermit product code and I have no worktree
  slot (Invariant 1 bars feature work in the primary). Filed, not fixed.
- The binary is another slot's build (`worktrees/covnode` @ `fc49593ac`, 2026-08-05), not
  current main `f89c6976`. Gaps this structural are unlikely to have closed in a day, but
  they were not re-checked at main.
- `compare.py` implements the canonical policy's tier-1 (strip wall-clock prefix) and exact
  comparison of the remainder. It does **not** implement `<hostaddr>` ordinalization —
  currently moot, since detcore has zero production `host_addr()` call sites.
- Stack comparison is over the `[memory]…[stack]` DETLOG records only, and those carry the
  `dtid` defect too, so `stack DIVERGE` is currently explained by GAP-3 and is not
  independent evidence of a memory-content difference.
- The two captured `/etc/hostname` stdout files are redacted before publication. The
  experiment compares logging structure, not a host identity, and neither result table
  depends on those bytes.
