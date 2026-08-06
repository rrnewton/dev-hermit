# /proc and /sys read determinism: no leak in the enumerated set — but self CPU-time is frozen

**Task:** `proc-sys-read-determinism` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Result

**27 of 27 enumerated /proc and /sys paths are stable across separate hermit invocations, and the
stability is earned, not vacuous.** No host-nondeterminism leak was found in this set.

The residual finding runs the *other* way: `/proc/self/stat` utime/stime are **frozen at zero**, which
is the #140 anti-pattern (deterministic but with evolution destroyed) rather than a leak.

## Method and the anti-vacuity controls

Guest `/bin/cat <path>` under `ptrace --strict --no-virtualize-cpuid --max-timeslice=disabled`, run as
**two separate hermit invocations**, comparing the SHA-256 of stdout. Cross-invocation is the right
test here: a value that is stable *within* one invocation but varies *between* them would pass
hermit's own `--verify` and still be a leak.

"Stable" proves nothing on its own, so three controls:

| control | result |
| --- | --- |
| does the **host** value vary? | **6 of 6 probed VARY** (`uptime`, `loadavg`, `stat`, `meminfo`, `self/stat`, `interrupts`) read 1 s apart |
| are the guest hashes **path-specific**? | 6 distinct hashes — not one canned response |
| are the values **plausible**? | `/proc/uptime` → `121.00 0.00`; `/proc/loadavg` → `0.00 0.00 0.00 1/1 1` — virtualized, not host values |

So the host varies, the guest doesn't, and each path returns its own plausible virtualized content.
The stability is real virtualization.

## The #140 check, and where it splits

The task warns: do not "fix" by freezing to a constant if that destroys continuous evolution. Two paths
behave **differently**, and the contrast is the finding:

| path | within one run | verdict |
| --- | --- | --- |
| `/proc/uptime` | `124.00` → `227.00` → `284.00` across a busy loop then a sleep | **advances — deterministic *and* evolving. #140-correct.** |
| `/proc/self/stat` utime/stime | `0 0` → `0 0` across a 200 000-iteration shell loop | **frozen at zero** |

A guest that measures its own CPU consumption — a profiler, a benchmark harness, a scheduler-aware
workload — always sees `0`. That is deterministic, and it is exactly the shape #140 says not to reach
for: the value no longer evolves. Note virtual time advanced ~103 s in that same run, so this is not a
sub-tick rounding artifact.

I am **not** claiming this is a bug: it may be unimplemented rather than deliberately frozen, and
freezing self-CPU-time is a defensible determinism choice. It is flagged because the task asks for it
and because "frozen" and "correct" are not the same verdict.

## Scope limits (#213 asks for the full set; this is not it)

* **27 paths, not exhaustive.** `/proc` has hundreds of entries and `/sys` thousands. This is a
  *targeted candidate set* aimed at plausibly host-varying entries (`/proc/self/*` ×12, `/proc/*` ×8,
  `/proc/sys/kernel/*` ×4, `/sys/*` ×3) — full list in `candidate-set.txt`. A genuine full enumeration
  would walk the trees and is the natural follow-up.
* **ptrace only.** A leak could exist on DBI or KVM; not swept.
* **One read per file per run**, except the two intra-run evolution probes above.

## Provenance (#268)

Binary `worktrees/oci/hermit/target/release/hermit`, built 2026-08-06 04:30, `--features
third-party-backends`. `LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64`. Guest `/bin/cat` and
`/bin/sh` (both outside `/tmp`, which hermit overmounts).
