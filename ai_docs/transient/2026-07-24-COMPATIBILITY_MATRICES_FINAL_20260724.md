# Final Compatibility Matrices - 2026-07-24

This is the final overnight compatibility snapshot. It supersedes the
operational counts in the earlier 2026-07-23 and pre-10:12 UTC summaries; those
documents remain useful as historical, multi-SHA evidence.

## Evidence boundary

- Hermit fork `main`: `a2926507aafb9c922cbe230490f1cee5ebcea586`, the
  squash merge of [#562](https://github.com/rrnewton/hermit/pull/562). It also
  contains the record/replay repair from
  [#560](https://github.com/rrnewton/hermit/pull/560).
- Ptrace gate: exact-head `./validate.sh --no-label-pr` completed all 15 gates
  and all 118 strict probes. Strict results use the ptrace backend, default log
  level, and no relaxations.
- DBI: 38-program measurement at Hermit `3c49d197b4734a068860cb30954bc657b90abf09`.
- KVM and the complete pre-#560 record/replay matrix: Hermit
  `2df293bde92bded0893fbe5eb83a633453dabcb0`.
- PR #560 validation: its exact head
  `380e042325f069b6673c558c471ea9085c6eb1e8` repaired all 14 stdout-mismatch
  rows. The PR is merged into the Hermit `main` SHA above.

The denominators differ intentionally. They are the largest fully recorded
matrix for each backend or mode, not a claim that all backends ran the current
118-command gate.

## Final summary

| Backend or mode | Passed | Failed | Assurance and qualification |
|---|---:|---:|---|
| Ptrace strict gate on `main` | **118** | **0** | 118/118 at L2: `hermit run --strict --verify`, default log, no relaxations. |
| DBI 38-program expansion | **20** | **18** | DBI two-run verifier under `--strict --verify`; 17 lifecycle timeouts and one non-ELF wrapper. This verifier compares guest output and its DBI memory hash, not ptrace trace equivalence. |
| KVM 57-program matrix | **31** | **26** | 31/57 at L2, KVM backend, default log, no relaxations; zero timeouts. |
| Record/replay complete baseline | **36** | **21** | 36/57 output-correct at `2df293b`; all 57 recorded, 50 replays exited zero. |
| Record/replay after #560 | **50** | **7** | Accounted result: the 36 baseline passes plus the 14 formerly mismatching rows that passed targeted record/replay retests. This was not a single full 57-row rerun on merged `main`. |
| Historical post-envp R/R batches | **166** | **53** | 219 prescribed historical cases across batches 1-21; separate from the common 57-program matrix. |

## Ptrace strict gate: 118/118

The nonblocking validation gate grew from 16 programs through
[#521](https://github.com/rrnewton/hermit/pull/521),
[#537](https://github.com/rrnewton/hermit/pull/537), and
[#542](https://github.com/rrnewton/hermit/pull/542). It reached 57 through
[#550](https://github.com/rrnewton/hermit/pull/550), later reached 61, added 30
programs through [#558](https://github.com/rrnewton/hermit/pull/558), and added
27 more through [#562](https://github.com/rrnewton/hermit/pull/562).

| Cohort | Count | Programs and operations |
|---|---:|---|
| Gate through 61 | 61 | `echo`, `seq`, `cat`, `wc`, `head`, `base64`, `id`, `lua`, `perl`, `awk`, `bc`, `sqlite3`, `bash`, `cargo`, `rustc`, `node`, `gcc`, `g++`, `make`, `bzip2`, `gzip`, `xz`, `zstd`, `openssl`, `sort`, `uniq`, `tr`, `cut`, `tee`, `paste`, `comm`, `join`, `find`, `stat`, `file`, `basename`, `dirname`, `env`, `printenv`, `uname`, `factor`, `expr`, `dd`, `df`, `du`, `hostname`, `whoami`, `groups`, `tty`, `nproc`, `arch`, `realpath`, `readlink`, `mktemp`, `sha256sum`, `sha1sum`, `md5sum`, `wc -l`, `nl`, `expand`, `unexpand`. |
| PR #558 expansion | 30 | `diff`, `patch`, `grep`, `egrep`, `fgrep`, `sed`, `tar`, `cp`, `mv`, `rm`, `mkdir`, `rmdir`, `touch`, `chmod`, `chown`, `ln`, `date`, `cal`, bounded `yes`, `tac`, `rev`, `fold`, `fmt`, `shuf`, `numfmt`, `csplit`, `split`, `install`, `mkfifo`, `cmp`. |
| PR #562 expansion | 27 | `test`, `[`, `printf`, `sleep 0`, `stdbuf`, `nohup`, `nice`, `ionice`, `taskset`, `chrt`, `flock`, side-effect-free `logger`, `getopt`, `column`, `hexdump`, `xxd`, `strings`, `od`, `sum`, `cksum`, `b2sum`, `tsort`, `ptx`, `pinky`, `logname`, `users`, `uptime`. |

Two residual observations are outside the 118/118 headline:

- `timeout 1 true` is deliberately excluded. Its parent waits in
  `rt_sigsuspend` while the child is delayed, so Run1 does not complete.
- The existing `node` probe failed one strict-only repetition because two
  worker threads opened shared libraries in a different order. It passed the
  full 118-probe validation repetition. Treat it as a known intermittent row,
  not evidence that the complete gate was stress-hardened to L4.

## DBI: 20/38

The DBI expansion used `hermit run --backend dbi --strict --verify` with the
default log level and no relaxations. Twenty programs completed the DBI
two-run verifier. Seventeen shell/child-process rows timed out and the `file`
wrapper was rejected as a non-ELF input.

The timeout investigation ruled out raw DynamoRIO cost: direct
`sort --version` passed in under one second, while a shell or exec path spun at
about 100% CPU without syscalls. GDB stopped in DynamoRIO's recursive global
allocation lock. Reverie's DBI guest injects handled clone/fork/exec syscalls
from the pre-syscall callback, bypassing DynamoRIO lifecycle handling and
leaving child lock/TLS state inconsistent.

Relevant landed work:

- Hermit [#234](https://github.com/rrnewton/hermit/pull/234) routes DBI through
  `DbiRunner`.
- Reverie [#48](https://github.com/rrnewton/reverie/pull/48) supports external
  tools.
- Reverie [#53](https://github.com/rrnewton/reverie/pull/53) and Hermit
  [#543](https://github.com/rrnewton/hermit/pull/543) repair application-syscall
  results.

Blocker: [Reverie issue #31](https://github.com/rrnewton/reverie/issues/31)
tracks native clone/exec lifecycle, child state, process-tree tracking, and
remaining clock/ppid stubs. The next meaningful DBI measurement is a same-SHA
rerun after that lifecycle work, first on the 38-program set and then on all
118 ptrace-gate rows.

## KVM: 31/57

Every row used `hermit run --backend kvm --strict --verify` with default
logging and no relaxations.

| Result | Programs |
|---|---|
| PASS L2 (31) | `echo`, `seq`, `cat`, `wc`, `head`, `base64`, `id`, `lua`, `perl`, `awk`, `sqlite3`, `bash`, `openssl`, `stat`, `basename`, `dirname`, `uname`, `factor`, `expr`, `du`, `hostname`, `whoami`, `groups`, `nproc`, `arch`, `realpath`, `readlink`, `sha256sum`, `sha1sum`, `md5sum`, `wc-lines`. |
| FAIL: clone/fork ENOSYS (19) | `bc`, `bzip2`, `gzip`, `xz`, `zstd`, `sort`, `uniq`, `tr`, `cut`, `tee`, `paste`, `comm`, `join`, `dd`, `tty`, `mktemp`, `nl`, `expand`, `unexpand`. |
| FAIL: execve ENOSYS (2) | `env`, `printenv`. |
| FAIL: ELF load-address overlap (2) | `cargo`, `rustc`. |
| FAIL: fd/directory gaps (2) | `find` needs `fcntl(F_SETFD)` and `fchdir`; `df` needs mount metadata access and `chdir`. |
| FAIL: script loader (1) | `file` resolves to a top-level shell script, but the KVM loader expects ELF. |

Relevant landed work includes Hermit
[#229](https://github.com/rrnewton/hermit/pull/229),
[#233](https://github.com/rrnewton/hermit/pull/233),
[#272](https://github.com/rrnewton/hermit/pull/272),
[#277](https://github.com/rrnewton/hermit/pull/277), and
[#544](https://github.com/rrnewton/hermit/pull/544). The exact matrix SHA is
the merge of [#553](https://github.com/rrnewton/hermit/pull/553). Reverie
dependencies include
[#50](https://github.com/rrnewton/reverie/pull/50),
[#52](https://github.com/rrnewton/reverie/pull/52), and
[#54](https://github.com/rrnewton/reverie/pull/54).

Blocker: [Reverie issue #55](https://github.com/rrnewton/reverie/issues/55)
tracks fork/clone and accounts for 19 of 26 failures. After process creation,
the next work is exec support, flexible ELF/interpreter placement, shebang
loading, and the remaining `fcntl`/directory/mount operations. Rerun all 118
current ptrace-gate rows after those changes.

## Record/replay: 36/57 baseline, 50/57 after #560

The complete matrix used private data directories and this per-row shape:

```text
timeout 60s target/release/hermit record start --data-dir DIR -- PROGRAM
timeout 60s target/release/hermit replay --autopilot --data-dir DIR
```

A pass requires recording exit 0, replay exit 0, and byte-identical guest
stdout. Record/replay uses the ptrace backend, default log level, and no
relaxations, but it is reported separately from the L1-L4 ladder.

| Result at `2df293b` | Programs |
|---|---|
| Output-correct PASS (36) | `echo`, `seq`, `cat`, `wc`, `head`, `base64`, `id`, `lua`, `perl`, `awk`, `sqlite3`, `bash`, `openssl`, `find`, `stat`, `file`, `basename`, `dirname`, `env`, `printenv`, `uname`, `factor`, `expr`, `df`, `du`, `hostname`, `whoami`, `groups`, `nproc`, `arch`, `realpath`, `readlink`, `sha256sum`, `sha1sum`, `md5sum`, `wc-lines`. |
| Replay exit 0, stdout mismatch (14) | `bc`, `bzip2`, `gzip`, `zstd`, `sort`, `uniq`, `tr`, `cut`, `tee`, `dd`, `tty`, `nl`, `expand`, `unexpand`. |
| Replay timeout (3) | `cargo`, `rustc`, `xz`. |
| Replay exit 1 after desync (4) | `paste`, `comm`, `join`, `mktemp`. |

Hermit [#560](https://github.com/rrnewton/hermit/pull/560) replayed successful
`dup2` calls so the live replay fd table follows the recording. All 14 prior
stdout-mismatch rows then recorded, replayed, and produced byte-identical
stdout. This raises the accounted output-correct set from 36 to 50. It does
not claim that a single full 57-row run occurred on merged `main`.

Remaining blockers:

- `cargo` and `rustc`: replay exhausts the event stream while expecting
  `EpollWait`, tracked by
  [Hermit issue #536](https://github.com/rrnewton/hermit/issues/536).
- `xz`, `paste`, `comm`, `join`, and `mktemp`: fd number/order divergence.
  [Hermit PR #555](https://github.com/rrnewton/hermit/pull/555) remains open
  and addresses physical fd closes during replay.
- The combined `record start --verify` wrapper still duplicates stdout for an
  external tty command-substitution probe even though separate record plus
  replay is byte-identical after #560.

Related landed changes are Hermit
[#551](https://github.com/rrnewton/hermit/pull/551) for direct strict recording,
[#552](https://github.com/rrnewton/hermit/pull/552) for late ELF interpreters,
[#557](https://github.com/rrnewton/hermit/pull/557) for SIGPIPE preservation,
and [#560](https://github.com/rrnewton/hermit/pull/560) for replay fd-table
tracking.

## PR index

All PRs below materially define the final matrices above.

| Area | Merged PRs | Open follow-up |
|---|---|---|
| Ptrace gate | Hermit [#521](https://github.com/rrnewton/hermit/pull/521), [#537](https://github.com/rrnewton/hermit/pull/537), [#542](https://github.com/rrnewton/hermit/pull/542), [#550](https://github.com/rrnewton/hermit/pull/550), [#558](https://github.com/rrnewton/hermit/pull/558), [#562](https://github.com/rrnewton/hermit/pull/562) | `timeout` Run1 hang and intermittent Node ordering need focused fixes, not gate expansion. |
| DBI | Hermit [#234](https://github.com/rrnewton/hermit/pull/234), [#543](https://github.com/rrnewton/hermit/pull/543); Reverie [#48](https://github.com/rrnewton/reverie/pull/48), [#53](https://github.com/rrnewton/reverie/pull/53) | Reverie issue [#31](https://github.com/rrnewton/reverie/issues/31). Hermit #265 was closed without merge and is not counted as landed work. |
| KVM | Hermit [#229](https://github.com/rrnewton/hermit/pull/229), [#233](https://github.com/rrnewton/hermit/pull/233), [#272](https://github.com/rrnewton/hermit/pull/272), [#277](https://github.com/rrnewton/hermit/pull/277), [#544](https://github.com/rrnewton/hermit/pull/544), [#553](https://github.com/rrnewton/hermit/pull/553); Reverie [#50](https://github.com/rrnewton/reverie/pull/50), [#52](https://github.com/rrnewton/reverie/pull/52), [#54](https://github.com/rrnewton/reverie/pull/54) | Reverie issue [#55](https://github.com/rrnewton/reverie/issues/55). |
| Record/replay | Hermit [#551](https://github.com/rrnewton/hermit/pull/551), [#552](https://github.com/rrnewton/hermit/pull/552), [#557](https://github.com/rrnewton/hermit/pull/557), [#560](https://github.com/rrnewton/hermit/pull/560) | Hermit [#555](https://github.com/rrnewton/hermit/pull/555) and issue [#536](https://github.com/rrnewton/hermit/issues/536). |

## Ordered next steps

1. Land or supersede #555, then rerun all seven remaining R/R failures and the
   complete 57-row matrix on one merged `main` SHA.
2. Implement Reverie DBI lifecycle issue #31 and rerun the 38-row DBI set
   before expanding DBI to the 118-row ptrace gate.
3. Implement KVM fork/clone issue #55, then exec/loader and fd/directory gaps;
   rerun the full 118-row gate under KVM.
4. Fix the ptrace `timeout 1 true` scheduler interaction and stress the Node
   row before describing the 118-row gate as L4.
5. Run ptrace, DBI, KVM, and R/R against one common program corpus and exact
   commit so future ratios compare behavior rather than different denominators.
