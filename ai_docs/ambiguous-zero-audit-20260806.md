# Ambiguous-zero audit: signals where "nothing happened" reads as "all good"

Date: 2026-08-06 · Agent: hermit-verify · Task: `ambiguous-zero-audit-across-signals`

The question asked of every candidate: **can this value distinguish "the thing did
not need to happen" from "the thing never ran"?** Where it cannot, the fix is a
*positive signal* — a count of work actually done, an explicit NOT-MEASURED
state, or a separate did-run flag. A zero that means two things cannot be
repaired by printing it more loudly.

Every row below was **read in the source**, not just grepped. Rows are marked
`VERIFIED LIVE` where I reproduced the behaviour, `VERIFIED BY READING` where the
control flow is unambiguous, and `NEEDS AUDIT` where the discriminator exists but
I did not confirm every consumer reads it.

Scope: the three surfaces named as highest blast radius — compat scorecard,
prefix-depth ratchet, landing gates — plus the backend banners that feed the
first two. Not covered: hermit product internals at large, reverie, liteinst2,
agent-utils. Those need their own pass.

---

## Tier 1 — these become published numbers

### A1. `render-scorecard.rs`: an empty denominator renders as `TOTAL 0`
**VERIFIED LIVE.** Running the renderer on the current
`compat-envelope/scorecard.csv` prints the full caveat preamble and then:

```
bucket                  ptrace
------------------------------
------------------------------
TOTAL                        0
```

The latest run (`backend-parity-fc49593ac21c-…`) contains **only dbi rows in
`strict` mode** — 26 pass, 2 gap, no ptrace rows and no `verify` rows — so the
default `verify`-ptrace denominator is legitimately empty. The output cannot say
that. A reader sees `TOTAL 0`.

The per-cell vocabulary (`?`, `~`, `n/a`) is good and does not help here, because
there are no cells at all: this is the *denominator* being empty, one level up.

**Discriminator:** when every bucket's denominator is empty, refuse to render a
table. Emit `NO DATA: run <id> has 0 ptrace/<mode> passing cells (modes present:
strict; backends present: dbi)` and exit with a distinct status. The facts needed
are already in hand at that point.

### A2. `render-scorecard.rs` header contradicts its own code
**VERIFIED BY READING.** The module comment (line ~22) still says *"A cell the
backend never ran counts as 0 in both, so a small [percentage]…"*. The code no
longer does that: `BCell` carries `ran` and `par_measured`, and both
`ran_count` and `*_measured_count` reach the JSON and TSV output.

Not an ambiguous zero — an ambiguous *document*, which is how a fixed hazard gets
"re-fixed" or a correct number gets distrusted.

**Discriminator:** delete the stale sentence; state the `?`/`~`/`n/a` vocabulary
that actually ships.

### A3. `prefix_depth.sh`: the NO-RUN guard is AND-gated, so a silent no-op scores `Y=0`
**VERIFIED BY READING.** The guard is:

```bash
if [ "$(commits "$OUT/$tag.$be" | wc -l)" -eq 0 ] && [ "$rc" != 0 ]; then
  ... NO-RUN ...
```

Zero records **and** a nonzero exit. A backend that **exits 0 while emitting
nothing** falls through to `depth()`, whose awk prints `0` for an empty candidate
file — published as `Y=0`, "diverges at record 0".

That is not hypothetical for this fleet: sabre's documented failure mode is
`patched_sites=0` with a **silent ptrace fallback and `rc=0`**. Exactly the case
the `&&` lets through.

**Discriminator:** make it an OR — zero comparable records is NO-RUN *regardless
of exit status* — and print `emitted_records` as its own column beside `Y`, so
"emitted 3 of 14" and "matched 0 of 14" are never the same cell.

### A4. `prefix_depth.sh`: `Z` is never guarded, so a failed golden reads as a perfect self-match
**VERIFIED BY READING.** `Z=$(commits "$OUT/$tag.ptrace" | wc -l)` with no check.
If the golden ptrace run fails, `Z=0` and the script prints the self-reference row
as `Y=0 Z=0` — which renders as the golden matching itself, the most reassuring
possible line — and then every backend row as `Y/0`.

**Discriminator:** treat `Z == 0` as fatal for that guest. Print
`NO-GOLDEN <guest> rc=<grc>` and skip its backend rows entirely; never emit a row
whose denominator is zero.

### A5. `prefix_depth.sh`: `commits()` ingests stderr, which carries a backend-only banner
**VERIFIED BY READING**, and already reported on
`prefix-parity-depth-ratchet-metric`. `commits()` cats both `.log` and `.err`;
the `:: Backend: …` banner is printed to stderr for KVM/LiteInst only
(`run.rs:2809-2818`), so those two backends can ingest a line the ptrace golden
can never contain. Listed here for completeness of the ratchet's zero-story.

**Discriminator:** read the INFO log only; stderr is a presentation channel.

---

## Tier 2 — backend banners feeding the scorecard

### B1. `record_start.rs`: `mapped_sites=0` is a literal in the non-ELF branch
**VERIFIED BY READING.** For a non-ELF target the banner is a fixed string:

```
:: Backend: e9patch preprocessing + ptrace record runtime; mapped_sites=0;
   main_executable=non-ELF; preprocessing=not-applicable
```

The `0` is a frozen constant, not a measurement. A human reading the whole line
is told why; **a consumer keyed on `mapped_sites=` reads a measured zero.** The
discriminator exists only in adjacent prose.

**Discriminator:** emit `mapped_sites=n/a` (or a separate
`preprocessing_attempted=false` field) so the not-measured state is in the field,
not in the sentence next to it.

### B2. `record_start.rs`: `rewrite_cache="not-applicable"` asserts an unverified reason
**VERIFIED BY READING.** `let rewrite_cache = if prepared.patched_sites == 0 {
"not-applicable" }`. That label claims *there was nothing to patch*. It fires
equally when there **were** candidates and none got patched — a degradation.

The discriminator is already computed one line away: `candidate_sites` is printed
in the same banner.

**Discriminator:** gate the label on `candidate_sites == 0`. When
`candidate_sites > 0 && patched_sites == 0`, say `patched-none-of-N`, which is a
degradation report, not a cache status.

### B3. sabre `PathEvidence.ptrace_fallback_sites = 0` — mitigated, but audit the consumers
**NEEDS AUDIT.** `ptrace_fallback_sites: self.patched_sites.len()` — zero means
"no fallback happened", which is also what you get if the run never reached the
point of falling back. **The same struct already carries a did-run flag,
`guest_rpc_observed`**, which is the right shape.

`hermit/ci/test_harness.sh` consumes it correctly, including the anti-vacuity
guard `length > 0 and all(.[]; .guest_rpc_observed)`. Whether *every* consumer
does is unverified.

**Discriminator:** already exists — require `guest_rpc_observed` at every site
that reads `ptrace_fallback_sites`, and audit the call sites.

---

## Positive exemplars — copy these rather than inventing a fourth pattern

These are already right, and they are the templates for the fixes above:

| pattern | where |
|---|---|
| `planned_test_nodes > 0 && zero_executed_nodes.is_empty() && absent_nodes.is_empty()`, and "a receipt carrying NEITHER count proves nothing and is rejected" | `ci-hub/lib/qualifying_receipt.rs`, `records.rs` |
| `executed_tests == 0` → downgrade to `no_result`, never certified as a pass | `ci-hub/validate/aggregate.py:345` |
| `length > 0 and all(…)` — an empty list is not "all true" | `hermit/ci/test_harness.sh:1359` |
| empty input → `exit 2`, a status distinct from both pass and fail | `compat-envelope/check-determinism-earned.sh` |
| cell vocabulary `?` unknown / `~` partial / `n/a` not-runnable, plus `ran_count` and `*_measured_count` in JSON and TSV | `compat-envelope/render-scorecard.rs` |
| `outcome=unavailable` + a free-text `reason` column | `compat-envelope/scorecard.csv` schema |

The landing gate is the most hardened surface in the repo on this axis — it has
been attacked the most. The ratchet is the least, and it is the newest.

## The general rule this audit suggests

A count is only self-describing if it travels with the size of the thing it
counted. `Y` needs `Z`; `patched_sites` needs `candidate_sites`; `executed_tests`
needs `planned_test_nodes`. Where the pair exists the ambiguity mostly
disappears; every Tier-1 and Tier-2 finding above is either a missing denominator
or a denominator that was computed and then not consulted.

## Follow-up

Fixing these is out of scope here — the list is the deliverable. Suggested order,
by blast radius: **A3, A4** (ratchet numbers are being published now and are the
newest, least-attacked code), then **A1** (silent empty scorecard), then **B2,
B1**, then **A2, A5** (documentation and channel hygiene), then the **B3**
consumer audit.
