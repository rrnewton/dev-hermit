# The strictness-tightening program: hole register, ratchet order, and the verify bar for each

**Task:** `strictness-tightening-program-the-p0-after-the-drain` (P0 umbrella, owner)
**Date:** 2026-08-05
**Scope:** local design and analysis. No `validate` run, no egress, no product change.

This is the phase-2 → phase-3 bridge. It consolidates the strictness holes found across
four prior passes into one ranked register, corrects the umbrella's ordering where the
state has moved, and states the entry condition and exit bar for each stage — so
"ratchet compat up" has a definition before anyone ratchets.

**Sources consolidated:** `verify-strip-site-audit-20260805.md` (25 strip sites),
`correctness-oracle-design-beyond-ptrace-parity-20260805.md` (oracle taxonomy),
`heap-domain-definition-guest-allocated-pages-20260805.md` (heap domain),
`qualified-rows-guard-mutation-bracket-20260805.md` (ledger guard).

---

## 1. Status correction — two things the umbrella says are now wrong

The umbrella text dates from 2026-08-04. Verified against the tree today:

| Umbrella step | Text says | Actual state |
| --- | --- | --- |
| (2) build the canonicalize mechanism | "THIS IS BUILT NOW" | **DONE.** `canonicalize-dont-strip-…` is CLOSED; #1595 merged as `9b642f6d3`, confirmed an ancestor of hermit `main` HEAD. |
| (1) `no-worse-ratchet` during the drain | "NOW, DURING THE DRAIN" | **NEVER SHIPPED.** `no-worse-ratchet-during-sprint-no-new-stripped-greens` is still **OPEN**. |

The second is the one that matters. Step 1 existed specifically to stop the hole
deepening *while* the drain ran. It did not ship, and the drain has run for a sprint. So
every green landed since 2026-08-04 rests on the same stripped comparison as the ones
before it, and the "existing debt is TOLERATED and not re-audited" carve-out has been
silently absorbing new debt rather than freezing old debt.

**Consequence for step 4:** the reset cannot assume the post-2026-08-04 cells are any
better than the pre- ones. There is no clean cut-off date to ratchet from.

Other member-task states (from the task store): `green-reset-…-on-bitwise` = BACKLOG;
`parity-definition-is-wrong-stdout-not-bitwise` = CLOSED;
`hash-the-register-file-and-sample-only-at-guest-logical-control-points` = OPEN;
`wire_the_coverage_node` = OPEN.

---

## 2. The critical-path correction: step 4 is blocked on something not in the plan

> **Step 4 as written cannot execute. There is no cross-backend comparator.**

The umbrella's step 4 is "recompute EVERY backend's parity AND determinism numbers under
the new bitwise contract". #1595 delivered the *contract* (`BitwiseInfoV1`,
`ComparisonSpec`, `--verify-strict`, `bitwise_parity` in `--verify-json`) — but it wired
it only into the **same-backend** paths. Verified: `compare_two_runs` has exactly two
live callers, `run.rs:2759` (one backend, run twice) and `record_start.rs:452` (record vs
replay); the two in `verify.rs` are tests.

So today the product can answer *"is this backend self-consistent, bitwise?"* and cannot
answer *"does this backend match ptrace, bitwise?"* — which is the entire parity axis.

- The **determinism** half of step 4 is executable now (self-verify under `--verify-strict`).
- The **parity** half is not. It requires new product code: a comparator that takes two
  executions from *different* backends and applies the same `BitwiseInfoV1` policy, plus
  durable per-run artifacts for it to dereference.

This is a missing stage between the umbrella's (3) and (4), and it is the longest pole in
the program. Everything else on this list is small by comparison.

---

## 3. The strictness-hole register

Ranked. Ranking method: **blocking-ness first** (does a later stage structurally require
it), then **reach** (what share of the green population rests on it), then **cost**. A
hole that blocks a stage outranks a wider hole that does not, because the wider hole can
be measured later while a blocker stops the program.

| # | Hole | Reach / denominator | Cost | Blocks |
| --- | --- | --- | --- | --- |
| **H1** | No cross-backend comparator exists (`compare_two_runs` is same-backend only) | 895/895 comparable cells | **high** (new product code) | Stage D — the whole parity axis |
| **H2** | Default `--verify` selects the lossy `Stripped` comparator | every bare `--verify`: 168 `validate.sh` call sites, 1200/1200 scorecard rows | low | Stages B, D |
| **H3** | FullTrace does not apply the poll-retry filters (`is_internal_io_poll_commit`, `is_scheduler_committed_time` live in `filter_deterministic`, which `FullTrace` never calls) | every nonblocking-I/O guest under `--verify-strict` | low–med | Stage D (else the reset is noise-dominated) |
| **H4** | Cross-backend "parity" is sha256 of guest stdout only | 895/895 cells; all 669 wins | low once H1 lands | Stage D |
| **H5** | Heap/stack domain is the brk segment only — captures **0.2%** of a program's non-exec anonymous memory | 0/1200 rows carry memory evidence today | med | Stage E; any heap-bearing bitwise claim |
| **H6** | `--detlog-stack/--detlog-heap` default off and never passed by the collectors | 0/1200 rows | trivial | Stage E |
| **H7** | `validate.sh` hardcodes `assurance=L2` before comparing, on bare `--verify` | 168 call-site lines, a **blocking** gate | low | reporting integrity |
| **H8** | `no-worse-ratchet` never shipped; new greens still rest on stripped compares | all cells landed since 2026-08-04 | low | Stage A (it *is* Stage A) |
| **H9** | Only 3 absolute oracles exist; corpus contains no harvestable others | 15/895 oracle-qualified | high (authoring) | Stage F — correctness, not strictness |
| **H10** | All 5 backends share one Detcore ⇒ parity is blind to determinism bugs **by construction** | structural | n/a | reframes what the reset number *means* |
| **H11** | `Fstat` logged without its output struct (FIXME T136880615) | all backends, all strictness | low | a comparator cannot fix it |
| **H12** | `strip_log_entry` RE2 `/tmp/.*"` is greedy — eats to the last quote on the line | `Stripped` mode only (diagnostic after H2) | trivial | nothing |
| **H13** | Register file is not hashed at all | all cells | high (net-new) | Stage F |
| **H14** | Sampling boundary is not defined as guest-logical-control | patching backends | med | Stage E |
| **H15** | KVM is output-only (`compare_logs=false`) — legitimate, but the row cannot express it | 200/200 KVM rows | low | Stage D bookkeeping |
| **H16** | `_envelope_level` reports L4 as 20 reps of a `Stripped` compare | envelope.json counters | low | reporting integrity |

H10 is listed because it must be *stated*, not fixed: no amount of strictness makes an
equivalence test detect a shared bug. It is why Stage F exists.

---

## 4. The ratchet order

Six stages. Each has an **entry condition** (what must already be true) and an **exit
bar** (the evidence that closes it, both directions). Stages A–C can run during or
immediately after the drain; D is the reset; E and F extend the contract afterwards.

### Stage A — freeze the debt (H8)
*Entry:* none. This should have run a sprint ago.
*Work:* land `no-worse-ratchet`: no NEW green claim may rest on a stripped comparison; no
NEW scorecard cell unless it carries the bitwise contract. Existing debt tolerated, not
re-audited.
*Exit bar:* a planted new-style stripped green is **refused** by the gate, and a planted
bitwise-qualified green is **accepted** (positive control — a gate that refuses everything
is not a ratchet). State both counts.

### Stage B — re-key the consumers onto the landed contract (H2, H7, H16, H12)
*Entry:* #1595 landed. **Satisfied.**
*Work:* flip `validate.sh:strict_compatibility_probe` (168 sites) and `_envelope_level`
to `--verify-strict --verify-json`, gate on `rr_report_has_bitwise_parity` (already exists
and already bracketed at `validate.sh:2712` — copy `rr_compatibility_probe`, do not
reinvent). Fix or delete the greedy RE2.
*Exit bar:* every flipped site consumes the typed field, never the process exit code;
each green→red flip is **filed as a finding**, never masked by widening the comparison
back. Report the flip count — it has never been measured.

### Stage C — make `--verify-strict` usable on real guests (H3)
*Entry:* Stage B (otherwise nobody is running `--verify-strict` at scale).
*Work:* decide, and record in the contract, how the poll-retry bookkeeping is handled
under `FullTrace` — producer-side suppression (as already done for the retry time-advance
at `scheduler.rs:2811-2816`) or a **declared, receipt-carried** exclusion. Silently
reusing `filter_deterministic` under FullTrace would be re-widening the comparison.
*Exit bar:* a nonblocking-I/O guest compares EQUAL across repeated runs under load
(retry counts vary, verdict does not), and a planted real divergence in the same guest
compares UNEQUAL. Both directions, on a guest that actually polls.

### Stage D — build the comparator, then reset (H1, H4, H15, H8-consequence)
*Entry:* Stages B and C. **This is the longest pole.**
*Work:* (i) a shared backend-vs-ptrace comparator applying `BitwiseInfoV1`, with durable
per-run artifacts it can dereference; (ii) rewire `collect-fullcorpus.sh` /
`collect-envelope.rs` off stdout hashing onto it; (iii) re-run the corpus; (iv) publish
the shrunken figure as the real baseline.
*Exit bar:* every cell carries `{contract, result, receipt}`; a cell with missing or
non-dereferenceable evidence renders **UNQUALIFIED, never green**; KVM's output-only
cells are recorded as such and are **not** counted on the bitwise axis. Mutation bracket:
a planted one-byte INFO divergence flips a cell red; a wall-clock-prefix-only difference
does not.

### Stage E — extend the contract to memory (H5, H6, H14)
*Entry:* Stage D (a memory leg is meaningless until the INFO leg is real).
*Work:* implement the heap domain as *guest-allocated pages* (provenance rule preferred —
the interval-model substrate already exists in `memory_metadata`), turn the flags on in
the collectors, define the sampling boundary as guest-logical-control.
*Exit bar:* `region_count == 0` is a **NO-RESULT, never a match** — this is the specific
trap, because today's domain is 0.2% full and would "match" trivially. Compare
`(address-range, digest)` pairs, not bare digests. Planted one-byte heap mutation ⇒
UNEQUAL; benign address-only difference ⇒ EQUAL.

### Stage F — correctness beyond equivalence (H9, H10, H11, H13)
*Entry:* Stage D. Independent of E.
*Work:* per the oracle design — count the oracles that already exist (`sysinfo-uptime`,
record/replay), add perturbed `--verify` (the one proposal with corpus-wide reach for a
fixed cost), author O1 determinization constants, then convert tier-A fixtures to
self-validating guests. Fix `Fstat` output logging. Register hashing is net-new.
*Exit bar:* every scorecard cell reports **two** numbers — bitwise-qualified and
oracle-qualified — with the oracle's **class** recorded, and no oracle counts without a
dereferenced negative control.

---

## 5. What "ratcheting compat up" requires

The owner's framing — *"it's fine if the compat envelope shrinks to reflect reality and
then when it is solid it should monotonically increase"* — needs three things to be true
before "monotonically increase" is meaningful. None is true today:

1. **A stable definition.** A ratchet across a changing contract is not a ratchet. The
   floor must be versioned with the contract that produced it (`BitwiseInfoV1`,
   `guest-allocated/v1`), and a contract change resets the floor rather than inheriting it.
2. **A floor that cannot be gamed.** The floor must be computed from qualified rows only.
   Every existing floor mechanism keys on a metric that can go up by measuring less —
   stdout equality rises when a guest prints less; `filtered == 0` is not completeness.
3. **Two numbers, not one.** A single "parity %" cannot distinguish a real envelope from a
   vacuous one, and cannot distinguish agreement from correctness (H10). The ratchet must
   be on both axes independently; a rise in bitwise coverage with flat oracle coverage is
   a rise in *confidence about agreement*, not about correctness.

---

## 6. What the reset will actually report — stated in advance

So the drop is read as a correction, not a regression, and so nobody is surprised:

- **Bitwise-qualified today: 0/895.** The reset does not shrink 669 → some smaller number;
  on the bitwise axis it starts from **zero** and builds up. The 669 is a *different
  measurement* (stdout-hash equality), which should be retained and relabelled as a
  diagnostic, not deleted.
- **Oracle-qualified today: 15/895** (3 meminfo oracles × 5 backends).
- **High-confidence (both): 0/895.**
- **Memory evidence today: 0/1200 rows**, and the domain that would produce it is 0.2%
  full — so a naive Stage E would report a large *fake* pass.
- **Direction of first movement is DOWN on the legacy axis too:** cells credited on stdout
  hashes may diverge in INFO, so the legacy number should be expected to fall when
  recomputed honestly.
- LiteInst bitwise 0/108 is not special. Every backend is 0 on this axis. LiteInst was
  simply the first anyone checked.

---

## 7. Limitations

- **The gate condition is unverifiable from here.** Step 3 is "the PR storm clears";
  egress is down box-wide, so I could not query live PR state. The program's stage
  ordering does not depend on it, but the *start* of Stage D does.
- No `validate` run, no product change; nothing here is bracketed against a live
  execution. All exit bars are specified, none are demonstrated.
- Cost labels (low/med/high) are relative judgements from reading the code, not estimates
  from attempting the work.
- Member-task statuses were read from the local task store; another agent may have moved
  one since.
- The register consolidates my four prior passes. Holes in areas I did not audit —
  Reverie-side normalization, the 21 non-C corpus rows, the 23 `strcmp` oracle candidates,
  and record/replay beyond its comparator wiring — are **not** represented and should not
  be assumed absent.
