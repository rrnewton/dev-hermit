# Progress - Tuesday, August 4, 2026

**Headline:** The local validation path began producing real six-check evidence at scale, and the ledger learned to distinguish a failed product test from a run that executed almost nothing.

## What shipped
- **Recovered the validation producer.** Running bare `./validate.sh` inside an agent sandbox exited 3 in about nine seconds because BpfJailer denied cgroup creation. Launching through `systemd-run --user` produced 26 full, six-check green records on exact heads; each executed 760-961 tests. Those are real completed runs, not success badges over zero tests.
- **Reclassified false reds from measured execution counts.** The landed ledger classifier re-evaluated 218 raw fail/timeout rows: 171 had executed one test or none and became `NO-RESULT`; 20 partial runs became `NEEDS-RERUN`; 17 were truncated; only 10 remained durable failures. The classifier keeps a positive control: a 765-test failure still remains failed.
- **Made validation receipts harder to counterfeit or reuse.** Receipts now carry executed/filtered counts, per-node coverage, commit anchoring, and exact-head binding. The merge gate invalidates evidence when a push changes the head and fails closed when its verifier is unavailable.
- **Source-pinned the CI DAG runner.** Hermit now resolves the tracked Rust DAG runner rather than preferring an untracked prebuilt binary whose provenance cannot be recovered from Git.

## What it means
A green now says what ran, at which SHA, and with how many executed tests. A red that never reached the suite no longer condemns a healthy PR permanently. This turns the validation ledger from a list of exit codes into evidence that can support a merge decision.

## What's stuck
Rebasing and pushing still rewrites the PR head, which correctly invalidates any receipt for the old SHA; the required sequence is therefore rebase, push, then validate the pushed head. That dependency is a throughput cost, not a reason to weaken exact-head binding.

## Unlanded evidence
- [Hermit #1626](https://github.com/rrnewton/hermit/pull/1626) fixes transient `/run/user/<uid>` mount visibility in `findmnt` and volatile `/proc/self/numa_maps` page counters. At head `b045a8ae`, focused strict verification passed 10/10 for `findmnt` and 10/10 for `numa_maps`; it remains a draft and is not counted as shipped.
- [Hermit #1213](https://github.com/rrnewton/hermit/pull/1213) repeatedly produced full six-check green receipts, but dual-family review found real timerfd authority bugs, including virtual absolute deadlines replayed in the host clock domain and unsupported `select`/`pselect6`/`readv` paths. It remains open and is not counted as shipped.
