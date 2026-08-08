# Which GitHub surface should index per-SHA validation receipts

**Task:** `compare-github-validation-index-prototypes` · agent `hermit-w6` · 2026-08-07
**Inputs:** three completed prototypes (statuses, commit comments, Checks). All artifact hashes and live
claims re-verified before comparing — see §1.

## Recommendation

> **Primary: commit comments. Optional complement: commit statuses. Checks: not now.**
>
> Comments are the only feasible mechanism that preserves *history* — the thing "global validation history"
> asks for. Statuses may be added later as a compact latest-state UI signal, never as the history.
> **Neither is authority**: a consumer must dereference the receipt and run the canonical verifier.

## 1. Premise verification (done before any comparison)

| claim | source | re-verified |
|---|---|---|
| 3 prototype artifact hashes | producer notes | **3/3 match** byte-for-byte |
| 4 statuses on `4c0e507e`, one context, newest-first | status prototype | **match**, ids `51808558882/…9114/…9398/…9733` |
| combined-status = 1 context, `success` | status prototype | **match** |
| check-runs on that SHA = 0 | checks prototype | **match** |
| commit comments = 0 (cleanup claim) | comments prototype | **match** — it really did delete all 12 |
| token scopes `gist, read:org, repo, workflow` | all three | **match** |

All six hold. The producer notes are accurate.

## 2. Checks is not a candidate that scores badly — it cannot run

Re-probed live, because the whole comparison turns on it:

```
POST /repos/{owner}/{repo}/check-runs   ->  403 "You must authenticate via a GitHub App."
GET  .../check-runs (after)             ->  total_count = 0   (the refused POST created nothing)
```

Token is a classic `ghp_` PAT. Checks requires a GitHub App: creation, installation, private-key custody,
rotation, and a pinned `app.id` in every reader. That is an ownership cost paid *before* the first receipt is
published, for a mechanism whose only advantage here (annotations, rich output) this index does not use.

**And it carries a live footgun.** GitHub treats a required check as satisfied by success **or skipped or
neutral**. The prototype's index run is deliberately `neutral`, so if its name were ever added to branch
protection it would *pass* the gate while asserting nothing. Adoption would mean permanently guaranteeing an
inert name is never required — a standing invariant that cannot be enforced from inside this repo.

## 3. Head-to-head: same three events, both feasible mechanisms

Inert target `rrnewton/ai-agent-playground @ f9cd8b8a…` (not a branch head, 0 comments before).

| metric (3 publishes + readback) | statuses | comments |
|---|---:|---:|
| **rate-limit units** (truthful cost) | **4** | 6 |
| wall seconds | 2.75 | **2.60** |
| request bytes sent | **704** | 1367 |
| gh invocations | 5 | 4 |
| history rows visible for my 3 events | 3 | 3 |
| readback response bytes | 51,567 | **6,171** |

**Denominators, without which those response numbers mean nothing:** the statuses read returned **13 rows
(only 3 mine)** = 3,967 B/row; the comments read returned **3 rows** = 2,057 B/row. Statuses are ~1.9× heavier
per row, *and* `GET /statuses` has no server-side context filter — you fetch every context on the commit to
find yours, so read cost grows with other tools' contexts, not just your own.

Note `gh` invocations (5 vs 4) **invert** the true ordering (4 vs 6 units): `--paginate` issues N HTTP requests
inside one invocation. An invocation counter is a proxy; the rate-limit delta is the measurement.

## 4. The two facts that decide it

### Fact 1 — combined-status collapses multi-run history

Measured directly: `history_rows_for_ctx = 3`, `combined_rows_for_ctx = **1**`.

Three events from two host classes went in; the endpoint most consumers read reports one. Under a single
stable context a second machine's result silently overwrites the first. Both escapes are bad:

- **per-machine contexts** → unbounded namespace growth, exactly what the status prototype's own verify list
  set out to avoid;
- **always read full history, ignore combined** → you have re-implemented the comments model with a heavier
  payload and no delete.

For a *history*, this is disqualifying.

### Fact 2 — statuses cannot be rolled back

```
DELETE /repos/{owner}/{repo}/statuses/{id}   ->  404 Not Found
```

There is no delete endpoint. A published status is permanent and can only be superseded. Comments delete
cleanly. This matters for a fleet-scale machine publisher: a bad run leaves permanent residue on real commits.

I am the evidence — my 3 probe comments are gone; **my 3 probe statuses are still on that SHA and cannot be
removed.**

## 4b. Fleet-scale cost — and why it should be struck from the decision

Re-derived from the live ledger, not carried over. `ignored/validate-run-ledger.jsonl`: 665 rows spanning
359,981 s = **4.17 days**; qualifying (`exit_code==0` and a commit) **360 runs / 151 distinct SHAs**.

> **86.405 receipt writes/day** per-run, or **36.242/day** SHA-deduped.

**This disagrees with the Checks prototype's 27.652/day, and the disagreement is the finding.** That note used
"111 canonical qualifying rows / 109 distinct SHAs" from a 654-row snapshot; the ledger has grown by only 11
rows since, so a 3.1× gap is not drift — it is a **different qualifying predicate**. Three defensible rates
exist for the same store (27.652 canonical · 86.405 exit-0 per-run · 36.242 exit-0 SHA-deduped). A receipt
rate is meaningless without naming the predicate beside it.

Projected against the per-event costs measured in §3:

| | per event | per day @ 86.405 | share of the 5000/hr core ceiling |
|---|---:|---:|---:|
| statuses | 1.33 units · 235 B | 115.2 units · 19.8 KiB | 2.30% |
| comments | 2.00 units · 456 B | 172.8 units · 38.4 KiB | 3.46% |

**Cost does not discriminate.** At the highest of the three candidate rates, the *more expensive* mechanism
spends 3.46% of a single hour's budget across an entire day. Statuses' 1.5× cheaper publish was their
strongest measured advantage; at real scale it is worth nothing. Strike cost from the decision rather than
weighing it.

## 4c. Offline cache — pointers vs a self-describing record

One full index event serialised as JSON is **329 chars**.

| | payload capacity | event fits? | offline cache is… |
|---|---:|---|---|
| status `description` | **140** (measured: 140 ok / 141 rejected) | **no** — 329 > 140 | **pointers only** |
| comment `body` | **65,535** | yes, 0.50% of cap (~199 events if batched) | **self-describing** |

What actually fits in a status is 35 chars — `pass 760/760 devserver-kvm cc0f821e` — with everything else
behind `target_url`. So an index cached from statuses cannot answer "what did this SHA validate as, on which
host, with what counts" without a network round-trip per receipt. That is precisely the round-trip an offline
index exists to avoid.

## 5. Scored comparison

Weighted for the stated purpose (a per-SHA index of validation *history*). ✅ good / ➖ acceptable / ❌ blocking.

| axis | statuses | comments | Checks |
|---|---|---|---|
| feasible on current creds | ✅ | ✅ | ❌ 403, needs App |
| exact-SHA lookup | ✅ | ✅ | ✅ |
| **multiple runs / machines** | ❌ combined collapses to 1 | ✅ append-only, natural | ✅ |
| **append-only enrichment** | ❌ overwrite-per-context | ✅ proven 2-host | ✅ |
| **rollback / cleanup** | ❌ no delete, permanent | ✅ deletes cleanly | ➖ auto-deleted at 1000/name |
| receipt binding | ✅ mutation-proven | ✅ mutation-proven | ✅ 12/12 |
| least-privilege scope | ✅ `repo:status` (narrowest) | ➖ `repo` | ❌ App + Checks:write |
| query / pagination | ➖ no server-side filter | ✅ exact Link pagination | ➖ no `external_id` filter |
| read cost per row | ➖ 3,967 B | ✅ 2,057 B | ➖ |
| publish cost @ real scale | ➖ 2.30%/day — **irrelevant** | ➖ 3.46%/day — **irrelevant** | ➖ |
| **offline cache** | ❌ pointers only (140-char cap) | ✅ self-describing (65,535) | ✅ |
| UI surface | ✅ native, compact | ➖ notification noise | ✅ rich |
| operational burden | ✅ none | ✅ none | ❌ App key custody + rotation |
| branch-protection safety | ➖ context could be required | ✅ inert by construction | ❌ neutral silently satisfies |

Comments lose on scope breadth and UI noise. They win the four axes the purpose actually turns on —
multi-machine history, append-only enrichment, rollback, and offline cache. Publish cost, once projected to
the real 86.405 receipts/day, discriminates nothing (§4b) and is struck rather than weighed.

**The one honest argument against this recommendation**, unaddressed by any measurement here: `repo:status` is
a narrower scope than `repo`. Choosing comments costs real least privilege. If that ranks above history
preservation for the owner, statuses-plus-accepted-history-loss is the coherent alternative — but it must be
adopted knowing combined-status reports one row where three runs happened, and that nothing published can be
withdrawn.

## 6. Mutation tests (both directions, re-run at current hashes)

Checks prototype suite: **12/12 PASS**. Comments verifier, exercised directly:

| case | result |
|---|---|
| **POSITIVE (must accept)** | **ACCEPTED** |
| wrong `target_sha` | REFUSED@parse, names both SHAs |
| tampered receipt bytes | REFUSED@verify, `53e2f45a` vs `b343596928ad` |
| FQDN host | REFUSED@parse |
| no receipt hash | REFUSED@verify |

**Method note worth keeping.** My first run had *all five* cases refusing — including the positive — because
I guessed the schema string wrong and everything died at the same parse check. Four green negatives that
proved nothing. Only the positive control exposed it. The negatives are evidence now because each refuses for
its own distinct reason.

## 7. Migration and rollback

**Adopt (comments):**
1. Publish behind a `--index-publish` flag, default off; one comment per completed validate, marker-prefixed,
   JSON block, `event_id` for dedup.
2. **Privacy guard at the WRITER, fail-closed** — the comments prototype learned this the hard way: a
   read-side check refused to *index* an FQDN that was already published. A read-side check cannot unpublish.
3. Consumers dereference the receipt and run `ci-hub/validation/verify_receipt.sh`. Never trust the index.
4. Keep the local ledger authoritative for a full overlap period; the index is a cache.

**Rollback (comments):** delete by id; `GET comments` returns 0. Proven — 3/3 removed this session. The local
ledger is untouched, so rollback costs nothing but the deletes.

**If statuses are added later:** one stable context per `profile+schema`, ≤140-char description, and treat
combined-status as *latest state only*, never history. Accept that it is **not reversible** — that is the
price of the narrower scope and the native UI.

**Do not adopt Checks** until someone owns a least-privilege App. If it is ever adopted, the inert index name
must be permanently excluded from required checks.

## 8. Disposition

3/3 probe comments deleted (SHA back to 0). **3 probe statuses permanently retained** on the inert private
scratch SHA — recorded rather than hidden, and itself the Fact-2 evidence. No production file, branch
protection, required context, or landing authority was touched.
