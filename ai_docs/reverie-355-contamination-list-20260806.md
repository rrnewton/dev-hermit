# The #355 contamination list — and why the re-measurement cannot happen yet

**Task:** `re-measure-after-355-pin-bump-contaminated-figures-list` (P1) · **Date:** 2026-08-06
**Author:** hermit-design · Read-only: `git show` + the local ledger + the DAG. No build, no validate, no egress.
**Classifier:** `ignored/contamination-355.py` (machine-local; the query it implements is reproduced below)

**Headline: the premise is half right, and the half that is wrong matters.** The pin did bump — but
**zero recorded measurements are on a clean pin.** All 585 ledger rows sit in one of *two* broken
windows, and the pin that has both fixes has produced no rows yet.

---

## 1. Precondition check — is #355 actually in? (it was open, so I checked)

The sibling livelock task warned explicitly: *"whether #355 is even in this base is still open — you
cannot attribute a behaviour change to a fix that is not present."* So this came first.

**hermit builds against a Cargo pin, and the parent checks out a gitlink — they resolve independently:**

| pin | value | has #355 fix? | has the rollback restoration? |
| --- | --- | --- | --- |
| hermit **Cargo.lock** rev (what actually builds) | `9470712a` | **yes** | **yes** — `decode(reservation.status)?` at `notifier.rs:576` |
| parent **gitlink** (what is checked out) | `04a46b43` | yes | **no** |

So the *build* pin is sound, and the parent's checked-out reverie is one window behind it. Both halves
matter, because **#355 alone breaks the SyncWaitOwner rollback contract** — that is the finding the
completed sibling task `fix-355-syncwaitowner-rollback-contract` established, and it means "has #355"
is not the same question as "is sound".

---

## 2. A proxy error I made, caught before reporting

My first classifier tested for the substring `commit(WAIT_OWNER_NOTIFIER)` in `notifier.rs`. It
reported **every** pin as post-#355, including `d973a85b` — which the task states is the pin
`detcore_misc` livelocks on.

That result was wrong, and the reason is the defect this lane keeps finding: `git log -S` shows an
**earlier** commit (`a8195cf`, #270) also changed that string's count, so the substring matches
pre-#355 trees. **A substring is a proxy; the condition is ancestry.** Corrected discriminator:

```
FIX_ESRCH    = 7951770   "consume dead ptrace status on decode error to end ESRCH hot spin (#355)"
FIX_ROLLBACK = faf8a34   "restore SyncWaitOwner rollback-and-wake-cleanup on decode error"
```

With ancestry, `d973a85b` correctly classifies **PRE-#355** — corroborating the task's own claim, which
the proxy had contradicted.

---

## 3. The list, as a query rather than a memory

For each of the 241 distinct hermit commits in the ledger: resolve the reverie rev **that commit built
against** (its own `Cargo.lock`), then test both ancestries.

```
ledger rows: 585   distinct hermit commits: 241

  355_WITHOUT_ROLLBACK_FIX     332 rows
  PRE_355_ESRCH_LIVELOCK       239 rows
  COMMIT_OR_LOCK_UNAVAILABLE    14 rows
  CLEAN_BOTH_FIXES               0 rows      <-- the finding
```

| pin | window | commits |
| --- | --- | ---: |
| `79517704` | 355 without rollback fix | 128 |
| `d973a85b` | **pre-#355, ESRCH livelock** | 80 |
| `04a46b43` | 355 without rollback fix | 5 |
| `59115421` | 355 without rollback fix | 4 |
| `55f6876a` | 355 without rollback fix | 4 |
| `bfea4d5a` | **pre-#355** | 3 |
| `114b3dfc`, `f8841586` | 355 without rollback fix | 2, 2 |

**Every figure derived from this ledger was measured in a broken window.** The most-used pin
(`79517704`, 128 commits) is in the *second* window — ESRCH fixed, rollback contract broken — which the
sibling task established is a **different hang**, not a clean state. That window was never on anyone's
contamination list, because the list was written as "pre- vs post-#355".

---

## 4. Re-measurement status, item by item

The task says: do not close until every listed figure is re-measured or explicitly marked unaffected
with a reason.

| # | Figure | Status | Reason |
| --- | --- | --- | --- |
| 1 | Per-node memory caps (PR #1583) | **gap CLOSED, verified** | `detcore_misc` now carries `hard_mem_max_bytes: 17179869184` and `rss_baseline_bytes: 8589934592` in `ci/dag/portable.json`; **47/47** portable nodes declare a hard cap. The excluded node is no longer capless. |
| 2 | cpu_timeout declarations (47 nodes) | **NOT DONE — and the count is 0, not 46** | **`0/47` nodes declare a `cpu_timeout`.** No value was ever derived for `detcore_misc` *or anyone else*, so there is nothing contaminated to re-derive — the whole declaration set is absent. `detcore_misc` has only a wall `timeout: 600`, which is exactly the instrument that cannot see a livelock. |
| 3 | Critical-path from 51-node runner timings | **cannot re-measure** | needs a clean-pin DAG run; none exists (§3). The contaminated input is real: a 600 s wall-kill in the chain. |
| 4 | Green-time (0.88% portable / 7.99% merge-gate) | **cannot re-measure** | same — needs clean-pin runs. |
| 5 | Validate wall-time | **cannot re-measure; a comparison is available but is NOT controlled** | full-profile wall: pre-#355 **n=137, median 423 s**; 355-without-rollback **n=208, median 390 s**; clean **n=0**. ~8 % lower, but these are different commit populations under different host loads — **not** a fix effect, and I will not report it as one. |

**Items 3-5 are blocked on the same thing: no measurement has yet been taken on `9470712a`.** The
"re-measure after the bump" step has no clean data to draw on; it requires new runs, which need a quiet
host and a validate this session was directed not to run.

---

## 5. What to do, in order

1. **Bump the parent gitlink** from `04a46b43` to the build pin `9470712a` so the checked-out reverie
   is not a window behind what hermit compiles (§1).
2. **Confirm `detcore_misc` passes at 16-wide on `9470712a`** — the task's own verify. Standalone
   passing proves nothing; the livelock is load-dependent.
3. **Then** re-run items 3-5 and report the deltas. Until step 2, the delta is unmeasurable, not
   pending.
4. **Derive cpu_timeouts for all 47 nodes** (item 2). This is the instrument that separates a livelock
   from a slow test, and it is currently declared **nowhere** — a wall budget alone cannot see the
   failure this whole thread is about.

**Re-run the contamination query at any time** to see which rows are still in a broken window:

```
for each distinct ledger commit C:
    pin = reverie rev in `git -C hermit show C:Cargo.lock`
    pre-#355            if not merge-base --is-ancestor 7951770 pin
    355-without-rollback if ancestor(7951770) and not ancestor(faf8a34)
    clean               otherwise
```

---

## 6. Provenance

Every number above is from a local read, and none is a new measurement:

* **ledger** `ignored/validate-run-ledger.jsonl`, 585 rows, 241 distinct hermit commits, read
  2026-08-06. Wall figures are the recorded `real_seconds` of `profile == "full"` rows.
* **pins** from `git -C hermit show <commit>:Cargo.lock`; **ancestry** from
  `git -C reverie merge-base --is-ancestor`.
* **DAG** `hermit/ci/dag/portable.json` at hermit `f89c69766`: 47 steps.
* **Box/-j/reps for the wall figures:** *not uniform, and that is the point* — the 345 full-profile
  rows come from many commits, hosts, `-j` settings and concurrency levels. They are a population, not
  a controlled series, which is exactly why §4 item 5 refuses to read a fix effect out of the medians.
  A properly provenanced re-measurement (fixed box, fixed `-j`, stated reps, interleaved A/B) is what
  step 3 above owes.

---

## 7. Not established

* **No build, no validate, no run.** Nothing was re-measured; items 1-2 were settled by reading the
  DAG, and items 3-5 are reported as blocked.
* **`detcore_misc` has NOT been confirmed to pass at 16-wide on the clean pin.** That is the task's
  stated verify and it is undone.
* **The 14 `COMMIT_OR_LOCK_UNAVAILABLE` rows** are commits whose object or `Cargo.lock` I could not
  read locally; they are unclassified, not clean.
* The classifier assumes the reverie rev in a commit's `Cargo.lock` is what that run actually built
  against. A run using a local `[patch]` override — which at least one prior agent did — would be
  misclassified, and nothing in the ledger records a patch override.
* `faf8a34` is identified as the rollback restoration from its commit subject and the sibling task's
  handoff (which cites `786dd2d`, its pre-land form). I did not diff `faf8a34` to confirm it is the
  landed equivalent.
