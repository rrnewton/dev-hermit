# Dev transcript catch-up through 2026-08-04

## Result

The cultivated transcript corpus now contains every whole day from 2026-07-20
through 2026-08-04: **16/16 daily Markdown reports**, **16/16 paired JSON
caches**, and **2,298/2,298 source owner turns** (delta 0 on every day). The two
previously-missing days, **2026-08-03 (292 turns)** and **2026-08-04 (227
turns)**, were generated with the established two-stage pipeline
(`dev_transcripts/summarize.py` → `render.py`, orchestrated by
`gen_daily_transcripts.py`); the format was not changed.

Coverage previously ended at 2026-08-02 (the closed task
`dev-transcripts-catchup-through-aug2`). No partial day was rendered after
2026-08-04: at generation time (2026-08-05 01:07 EDT) the current day had only
50 source turns through 00:50 EDT, so per the existing "regenerate no partial
day" convention it was left ungenerated.

## ISO week labels

The repository uses ISO week numbering. 2026-08-03 is a Monday, so it opens a new
ISO week: this run created **`2026-W32`** (Aug 3–4 so far), following `2026-W30`
(Jul 20–26) and `2026-W31` (Jul 27–Aug 2). The pure-render stage re-emitted all
three weekly files from cache without re-summarizing.

## Before and after

Before this task, 14 daily reports existed (Jul 20 – Aug 2). This task generated
Aug 3 from all 292 source turns and Aug 4 from all 227 source turns, refreshed
the `2026-W31`/`2026-W32` weekly caches, and regenerated no partial day after
Aug 4.

Final source/cache counts:

| Day | Cached turns | Source turns | Delta |
|---|---:|---:|---:|
| 2026-07-20 | 22 | 22 | 0 |
| 2026-07-21 | 48 | 48 | 0 |
| 2026-07-22 | 219 | 219 | 0 |
| 2026-07-23 | 206 | 206 | 0 |
| 2026-07-24 | 153 | 153 | 0 |
| 2026-07-25 | 89 | 89 | 0 |
| 2026-07-26 | 97 | 97 | 0 |
| 2026-07-27 | 184 | 184 | 0 |
| 2026-07-28 | 152 | 152 | 0 |
| 2026-07-29 | 91 | 91 | 0 |
| 2026-07-30 | 83 | 83 | 0 |
| 2026-07-31 | 119 | 119 | 0 |
| 2026-08-01 | 122 | 122 | 0 |
| 2026-08-02 | 194 | 194 | 0 |
| **2026-08-03** | **292** | **292** | **0** |
| **2026-08-04** | **227** | **227** | **0** |
| **Total** | **2,298** | **2,298** | **0** |

Bucket distribution across the two new days (519 turns): `omit=137`,
`one_sentence=99`, `paragraph=257`, `full=26`, `unknown=0`.

## What the two days covered

These highlights are cross-checked against the repository, the validate ledger,
and the live GitHub PR state — not copied from any progress report. Every subject
below is greppable in the repo, and every relative number carries its absolute
anchor.

**The landing drain and the merge-gate policy change (Aug 3).** The day centered
on unblocking a large PR-landing drain. The required GitHub check
`Regular tests (GitHub-managed portable)` was blocking every merge; the owner
authorized changing the Hermit merge gate so its sole required condition became
locally-validated **OR** (portable green **AND** privileged green), removing the
standalone `Regular tests` requirement. The `DynamoRIO` / DBI backend's
cold-checkout kept failing with HTTP 403 — root-caused to the agent identity
lacking a `github.com` entry in its network-proxy allowlist, not to any product
defect in the DBI backend or in `validate.sh`.

**~85% of the days' recorded validate reds executed essentially no tests.**
Derived from `ignored/validate-run-ledger.jsonl` over the Aug 3–4 window
(2026-08-03T04:00Z – 2026-08-05T04:00Z): of **211** rows with `result=fail`,
**180 (85%)** executed **≤1 test** — 169 executed **zero** tests and 11 executed
exactly one — whereas genuine `full`-profile PASS runs in the same window
executed a **median of 741 tests** (range up to 961). Per day the low-execution
share was Aug 3 = 86/86 fails (100%) and Aug 4 = 94/125 fails (75%). A "fail"
that ran zero or one test is a contention/no-result artifact, not a product
failure; counting it as a red overstates breakage. (An in-flight snapshot earlier
in the window cited "23 of 51"; the ledger has since grown to 542 window rows and
the derived low-execution share is higher — the figures above are recomputed from
the current ledger with the denominator stated.)

**The validate producer path.** A bare `./validate.sh` launched from an agent
sandbox exits 3 in ~9 s having run nothing, because the sandbox's `BpfJailer`
denies it creating its own cgroup (tell: CPU/wall ≈ 1.0× with ~0 executed tests).
The working producer path is a transient `systemd-run --user` unit entered through
`ci-hub validate-run` (the sole admission point), which is what yields the
741-median full runs above.

**The drain's structural blocker is a circularity.** A validate receipt is keyed
to an exact head commit SHA, but rebasing a stale-base PR **rewrites** that head
SHA, which invalidates the receipt — so the rebase performed to unblock a PR
destroys the very green result the merge gate needs to see.

**Two determinism PRs in flight (state as of 2026-08-05T05:0xZ).**
- Hermit [#1626](https://github.com/rrnewton/hermit/pull/1626) (branch
  `fix/findmnt-transient-user-mounts`, head `b045a8ae`) — **OPEN**. Extends the
  detcore procfs sanitizers to strip transient host state that leaked through
  `findmnt --kernel` (numeric `/run/user/<uid>` login-session mounts) and
  `/proc/self/numa_maps` (host page-accounting fields `active`, `anon`, `dirty`,
  `mapped`, `mapmax`, `swapcache`, `writeback`, `N<node>`), while preserving
  mount records outside host user sessions and NUMA mapping identity.
- Hermit [#1213](https://github.com/rrnewton/hermit/pull/1213) (head `3425d08a`)
  — **OPEN**, and failed Codex review for reusing virtual absolute time in the
  host clock domain. Its check rollup is currently mixed (2 success, 4 failure, 2
  cancelled, 11 skipped); the "6-check green" was a transient earlier state, so
  the current rollup and the review outcome are both reported here rather than the
  peak.

## Hostname privacy

The generator's FQDN sanitizer runs before every JSON write and again at render.
The Aug 3–4 daily Markdown and JSON caches contain **zero fully-qualified
internal FQDNs** (no `atn<N>` / `pnb0` / `*.facebook.com` internal forms). Known
internal machine names appear only in their reduced short-name form (for example
`devbig014`, `devbig030`, `devbig176`), which is the sanitizer's intended output.
The generated `daily/` corpus remains gitignored (machine-local); this report is
the durable, main-reachable evidence.

## Generation integrity and cost

The cheap-model summarization path (`with-proxy claude -p --model sonnet`) was
probed before the run and returned cleanly (exit 0) — the ORC sandbox-flag
breakage that forced heuristic fallbacks during the Aug 1 catch-up did not recur.
The run used `SUMMARIZE_BATCH=8`, `SUMMARIZE_WORKERS=6`, `SUMMARIZE_TIMEOUT=420`;
all 519 turns classified with **zero malformed-JSON or timeout fallbacks**, and
source-to-cache reconciliation is exact (delta 0). The full two-day generation
measured:

- wall: **12 m 47.87 s**;
- CPU: **2,450.52 s** (1,587.91 s user + 862.61 s system);
- maximum RSS: **610,020 KiB**.

Because rendering is a separate no-LLM stage, any future format change re-runs
`render.py` over the cached JSON with no additional model cost.
