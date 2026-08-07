# 9 of 183, not 183 of 183: quantifying the hollow completions — and three broken premises

**Task:** `corpus-argv-blank-on-all-214-rows-completions-may-be-hollow`
**Method:** re-parse of the existing sweep + authoritative guest-arg channel + a 9-cell both-directions bracket.
The 214-row sweep was **not** re-run, per instruction.

**Sources**
- `scratch/w11-sweep-results.tsv` — 214 rows (215 lines incl. header), cols `test_id status rc wall_s cpu_s signature`
- `scratch/w11-strict-sweep.sh` — the consumer that produced them
- `compat-envelope/corpus/corpus-c.tsv` — 214 rows, 6 pipe-separated fields
- hermit `e808322385d41015a77eb27f898ffb1f7c6cd220` (on `origin/main`) — `scripts/manifest-to-commands.rs --guest-args`

## Headline

**9 of the 183 completions were hollow — 4.9%.** Not 183.

And the more important half: **0 of the 166 rc=0 completions are hollow.** The feared failure mode —
"a guest that needs an argument and receives none may exit 0 without exercising the thing under test" —
**did not occur anywhere in this corpus.** Every hollow cell is *visibly* hollow: it prints a usage banner
and exits non-zero in 0.01 s.

| population | count | note |
| --- | ---: | --- |
| corpus rows | 214 | |
| rows with a source present | 184 | 30 `NO-SOURCE` never ran |
| **COMPLETED** | **183** | the task's denominator |
| — of which rc = 0 | 166 | **0 hollow** |
| — of which rc ≠ 0 | 17 | **9 hollow** (53 % of the non-zero band) |
| REFUSED | 1 | |
| **hollow / completions** | **9 / 183 = 4.9 %** | |

The 9, with their swept exit codes — all at wall 0.01 s:

| cell | required arg | swept rc |
| --- | --- | ---: |
| `c-programs/epoll-determinism` | `multi` | 2 |
| `c-programs/ipc-determinism` | `pipe-order` | 2 |
| `c-programs/liteinst-advanced` | `threads` | 2 |
| `c-programs/mmap-determinism` | `multiple` | 2 |
| `c-programs/record-replay-lseek-seek-cur` | `README.md` | 2 |
| `c-programs/signal-determinism` | `itimer-delivery` | 2 |
| `c-programs/socket-ioctl-timestamp` | `v4-us` | 1 |
| `c-programs/thread-sync-determinism` | `cancellation` | 2 |
| `determinism-stress-c/thread-contention` | `contention` | 1 |

### Both-directions bracket (9 cells, targeted — not a sweep re-run)

| direction | result |
| --- | --- |
| **negative** — run bare, as swept | usage banner on **9/9**, non-zero exit, rc reproduces the swept value exactly (2×7, 1×2) |
| **positive** — run with the declared arg | **rc = 0 on 9/9**, guest exercises |

The positive half matters: it proves the argument is the whole difference, so these are recoverable
measurements rather than broken guests. It also confirms the channel's declared values are correct.

### Why 9 and not more — the other 19 argv users are legitimate

28 of the 184 present sources reference `argc`/`argv`. Nine are the manifest-declared set above. The other
**19 are not hollow**, and this was checked per file rather than assumed:

- **self re-exec modes** — the guest `exec`s *itself* with a mode word: `dbi-pid-virtualization`
  (`--exec-child`), `dbi-unsupported-syscall` (`after-exec`), `record-replay-fd-close` (`--after-exec`),
  `vforkexec` (`child`). Bare invocation *is* the parent path.
- **optional flags with a valid default** — `arch-prctl-determinism` (`--host-cpuid`), `madvise-determinism`
  (`--kvm`/`--record`), `random-sources` (`--root-only`), `splice`/`tee`/`vmsplice-enosys` (`passthrough`),
  `writev-determinism` (`record`), `dbi-wait-lifecycle` (`--accounting-only`), `util-c/pmu-skid` (getopt).
- **bare is explicitly valid in the source** — `sched_yield_progress.c:75` treats `argc == 1` as a first-class
  path; `dbi_wait_lifecycle.c:35` errors only on a *wrong* argument, not a missing one.
- **signature only** — `nanosleep-par`, `sigtimedwait-{no-timeout,timeout-0s,timeout-1s}`, `print-memaddrs`
  declare `main(int argc, char **argv)` and never consult it (`print_memaddrs.c:15` uses `argc` as a
  multiplicand, not a switch).

`dbi-wait-lifecycle` (rc=1) and `util-c/pmu-skid` (rc=1) are therefore **genuine failures, not argument
starvation** — worth separating, because the tempting move is to attribute the whole non-zero band to this bug.

## Three premises that did not survive

**1. `723d19ad5` is not the guest-argument channel.** It is *"Ignore safe-ci-dag-runner's per-checkout profile
store"* — an 8-line `.gitignore` change. The real commit is **`e808322385d41015a77eb27f898ffb1f7c6cd220`**,
*"Give the e2e manifests a per-backend guest-argument channel"*, and it **is** on `origin/main`. Two further
copies exist off-main (`4b73e1d36`, `fa27c0b85`) — same subject, different SHAs, none of them the cited one.

**2. There is no `argv` column in `corpus-c.tsv`, so it cannot be "blank on all 214 rows".** Col4 is `extra`,
and the production collector appends it to the **compile** line as additional source files:

```sh
# compat-envelope/collect-fullcorpus.sh:211-214
for e in $extra; do extra_abs="$extra_abs $HROOT/$e"; done
cc -std=c11 -O2 ... "$HROOT/$prog" $extra_abs -o "$guest"
```

It is empty on 214/214 because every C-corpus guest is a single translation unit — that is the column working
correctly, not a data gap. The real defect is stronger and differently shaped: **the C corpus has no guest-argument
channel of any kind.** `collect-fullcorpus.sh:239` passes the guest bare:

```sh
measure "$backend" "$cell" "$lane" "$id" "$cell/guest"      # no argv, ever
```

**The asymmetry is the finding.** The *non-C* corpus does have the channel — `corpus-nonc.tsv` col3 is a full
command line and `measure ... $cmd` (`:251`) forwards it unquoted; **16 of its 21 rows carry arguments**
(`… /timed-progress-bar.sh --run`). So the corpus was split into two halves and only one kept the ability to
pass an argument. Adding an argv column to `corpus-c.tsv` would be the wrong repair: commit `e80832238`
explicitly routes the collector at `manifest-to-commands.rs --guest-args` so the corpus and the manifests
cannot drift into two copies of the same list.

**3. "183 completions must not be read as 183 measurements" overstates by ~20×.** The correct statement is
*174 of 183 are measurements; 9 are not, and all 9 announce themselves.*

## What the sweep artifact cannot tell you

`w11-strict-sweep.sh` sends both streams to `/dev/null` and derives `signature` **solely from rc**:

```sh
timeout -s KILL "$BUDGET" "$B" run --strict --tmp=/tmp -- "$bin" >/dev/null 2>&1
rc=$?
```

So `signature=ok` means exactly *rc was 0* — nothing about output. The task cites a sweep showing
`ERROR reverie_ptrace…` on line 1 **with rc=0**; that observation **can neither be confirmed nor refuted from
`w11-sweep-results.tsv`**, because no output was retained. If that rc=0-with-error case is real it is a
*separate* hollowness channel from the missing-argument one, and it would be invisible to every column here.
Closing it requires retaining stderr, not re-running with arguments.

Separately, this sweep reads col4 into a variable literally named `_extra` and never uses it
(`while IFS='|' read -r test_id src cflags _extra lane state`), then invokes the guest bare. So even a
populated argument column would not have reached the guest **through this consumer** — the manifest fix and
this script are independent breaks.

## Verify clause, answered

| clause | status |
| --- | --- |
| every row that needs an argument HAS one | **0 / 9** in the C corpus — no channel exists to carry it |
| a guest requiring an argument and denied one is REFUSED, not completed | **0 / 9** — the sweep classifies all nine `COMPLETED` (`rc != 0 → status=COMPLETED`, "ran to a verdict; not a refusal") |
| how many of the 183 completions were hollow | **9 / 183 = 4.9 %**, none silent |

The first two are code changes to the corpus collector and the sweep's classifier. This task was scoped to
measurement, so they are reported, not made.

## Cross-check against the upstream claim

Commit `e80832238` states nine corpus cells are false reds in every backend column (rrnewton/hermit#1815),
and that ptrace's true non-green count at hermit `82a8e8533575` is 12 rather than 21. The nine cells derived
here **independently from the `--guest-args` dump and confirmed by the bracket are the same nine** — the count
reproduces from a different direction, which is the reason to trust it.
