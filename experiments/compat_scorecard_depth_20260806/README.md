# Compat scorecard: the gap list is 16 cells, the denominator is 29%, and the deep bar isn't collectable for DBI

**Task:** `compat-scorecard-refresh-and-drive` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

**North star:** ratchet toward maximally-strict verify+parity 100%, judged on **full
detlog-stack/heap/INFO parity vs ptrace**, not stdout/exit.

## What I did and did not do

I **did not regenerate** the scorecard — that is a full corpus run, and two of the five backends can't
complete one on this host (KVM livelocks at guest startup; SaBRe returns `unavailable` on every cell).
What I did instead is the part that changes decisions: **analyse the existing scorecard for where the
gaps actually are**, and **probe whether the north star's deeper bar is even collectable today.** It
is not, for the largest backend — and that is the finding.

## 1. The gap list is smaller than it looks, and the denominator is the story

`compat-envelope/scorecard.csv`: **618 rows, 180 enabled** (422 disabled, 16 expansion). Every
parity%/det% headline is computed over those 180 — **29% of the rows.**

| backend | enabled | of rows | enabled % | note |
| --- | ---: | ---: | ---: | --- |
| dbi | 87 | 92 | **94.6%** | the well-covered backend |
| ptrace | 79 | 99 | 79.8% | the reference |
| kvm | 7 | 200 | **3.5%** | 177 disabled; 35 of them `kvm-run-fail-exit1` |
| **liteinst** | **0** | **220** | **0.0%** | the entire backend has **zero** enabled cells |
| sabre | 7 | 7 | 100% | but **0 pass** — all `unavailable` |

**Only 16 enabled cells are not `pass AND parity=1`:**

| backend | n | cells |
| --- | ---: | --- |
| dbi | 1 | `backend-parity/file_metadata` `parity=0` — the known #1549 fchown / virtual-root gap |
| kvm | 4 | `epoll-`, `madvise-`, `mmap-`, `thread-sync-determinism` — all `det=0` under `verify` |
| ptrace | 4 | parity blank — expected, ptrace *is* the reference |
| sabre | 7 | all `outcome=unavailable` — the backend does not run these cells at all |

**So "drive parity to 100%" on the current denominator is a 5-cell problem** (1 dbi + 4 kvm), and
finishing it would leave **liteinst at 0/220 and kvm at 7/200 still unmeasured**. The honest
prioritisation is therefore inverted from what the percentage suggests:

1. **liteinst: 0 enabled cells.** Nothing to ratchet. Enabling *any* is worth more than closing all 5.
2. **kvm: 3.5% enabled**, and blocked upstream by the KVM startup livelock (reverie `640c5bc`) — the
   35 `kvm-run-fail-exit1` disables are downstream of that, not 35 separate bugs.
3. **sabre: enabled but 0/7 pass** — a backend reporting `unavailable`, which is an absence, not a red.
4. **dbi `file_metadata`** — the one genuine, already-tracked product gap.

## 2. The north star's deeper bar is not collectable for DBI today

The scorecard's parity column is stdout-only, and that is now **honestly labelled**: the collector says
so at `collect-envelope.rs:432` (*"Capture guest stdout under ptrace and under backend; parity =
hashes match"*), and `REPORT.md` has been corrected to call it **`stdout parity`**, state that it is
*"an upper bound on four-signal cross-backend parity"*, and document a counterexample
(`backend-parity/exit_zero`). That earlier finding has been acted on; I am not re-reporting it.

What has **not** been established is whether the deeper comparison can be *collected*. I probed it:

| guest | stdout | exit | ptrace DETLOG | dbi DETLOG |
| --- | --- | --- | ---: | ---: |
| `/bin/true` | **MATCH** | 0/0 | 165 lines | **no log file produced** |
| `/bin/echo hi` | **MATCH** | 0/0 | 487 lines | **no log file produced** |
| `/bin/date +%Y` | **MATCH** | 0/0 | 523 lines | **no log file produced** |

**`--log-file` is silently ignored under the DBI backend.** The file is never created. My first read of
this was "DBI emits no DETLOG" — **that was wrong, and the correction matters**: DBI *does* emit
DETLOG (153 / 396 / 423 lines for the three guests), it writes it to **stderr** instead.

The consequence is the load-bearing part: **a parity harness that collects DETLOG via `--log-file` —
which is how the ptrace side is collected — gets an empty file for DBI, compares nothing, and reports
no differences.** That is a silent no-result wearing the appearance of a clean pass. It is the reason
the four-signal comparison has never been run for the backend holding 87 of the 180 enabled cells.

### And when you do collect it correctly, it diverges

Reading DBI's DETLOG from stderr and normalising both sides:

```
hermit log-diff --skip-commit  echo.ptrace.detlog  echo.dbi.detlog
  -> "Done processing logs, differences found."   (487 vs 396 DETLOG lines)
```

**`/bin/echo hi` — a guest whose stdout matches byte-for-byte and whose exit code matches — diverges at
DETLOG depth between ptrace and DBI.** That is a second measured counterexample beyond the one
`REPORT.md` documents, on a trivial guest.

**Honest limit on that claim:** some ptrace-vs-DBI DETLOG difference is *expected* — the backends
legitimately differ in syscall counts and DBI emits its own `reverie-dbi:` lines. I did **not**
attribute the 91-line delta line-by-line, so I am **not** claiming all of it is a determinism
divergence. What is established is narrower and sufficient: **the deep comparison is not currently
green, and nobody can tell how much of it is legitimate until the collection path is fixed.**

## 3. What to do, in priority order

1. **Fix DETLOG collection under DBI** — either honour `--log-file` on that backend, or make the
   parity harness read stderr for it. Until then the four-signal bar is unmeasurable for 87 of 180
   enabled cells, and any "deep parity" number would be computed over ptrace-only. *This is the
   blocker for the north star, and it is small.*
2. **Report the coverage denominator beside every parity%.** `parity 100%` over 180 of 618 rows, with
   liteinst at 0/220, is not what a reader takes it to mean. The REPORT already does this well for the
   *depth* axis; it should do the same for the *coverage* axis.
3. **Enable the first liteinst cells.** Zero is the only number on this scorecard that cannot be
   improved by ratcheting — it can only be improved by enabling.
4. **Treat the 35 `kvm-run-fail-exit1` disables as one upstream blocker**, not 35 gaps: they are
   downstream of the KVM startup livelock.
5. **Then** close the 5 genuine cell gaps (dbi `file_metadata`, 4 kvm determinism cells).

## 4. Infrastructure note worth carrying

The libunwind workaround at `/tmp/lu/usr/lib64` **was cleaned mid-session** and every `hermit` run on
this box fails `rc=127` without it. The RPM survives, so I re-extracted to a durable path:

```
cd ~/.local/hermit-deps/lu && rpm2cpio libunwind-1.8.0-4.el9.x86_64.rpm | cpio -idmu
export LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64
```

Anything under `/tmp` will be cleaned again; `~/.local/hermit-deps/lu` will not.

## Reproduction

```bash
export LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64
H=hermit/target/release/hermit
$H --log=info --log-file /tmp/p.log run --backend ptrace --strict --no-virtualize-cpuid \
   --max-timeslice=disabled --detlog-stack --detlog-heap -- /bin/echo hi
$H --log=info --log-file /tmp/d.log run --backend dbi    --strict --no-virtualize-cpuid \
   --max-timeslice=disabled --detlog-stack --detlog-heap -- /bin/echo hi 2> /tmp/d.err
ls /tmp/d.log            # ENOENT -- --log-file ignored under DBI
grep -c DETLOG /tmp/d.err   # 396 -- it went to stderr
grep DETLOG /tmp/p.log > /tmp/p.dl; grep DETLOG /tmp/d.err > /tmp/d.dl
$H log-diff --skip-commit /tmp/p.dl /tmp/d.dl   # "differences found"
```
