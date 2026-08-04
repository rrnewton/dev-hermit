# Proposal: ONE adversarial review on EVERY PR (dual only for core triggers)

Task: `one-adversarial-review-every-pr` (owner-directed policy change).
Author: `[coordinator, opus-4.8]` (hermit-226). Status: **analysis + proposed diff
for owner to apply** — the owner updates the canonical skills himself. Do not
overwrite the canonical files from this task.

Owner direction (verbatim intent): move to **one adversarial review mandatory on
every PR**, keeping **dual (claude + codex) only for the core-change triggers**;
delete the diverged duplicate skills in favor of **one canonical skill referenced
by explicit link**; make the reviewer's mandate central — **prove the claim, do
not confirm it**; preserve the label semantics and the comment-triage protocol;
fix the obsolete label and stale reference.

---

## 1. Current state (verified against primary sources, not prose)

### 1a. What is actually ENFORCED

`hermit/scripts/core-review-protocol-lint.sh` is the `core-review-protocol`
merge-gate job — the "code that never forgets," written after PR #1095 landed a
core change with no dual review. It enforces (lines 67-109):

- A PR **labeled `post-facto-human-review`** may land only with: one
  `adversarial-review-codex{1..4}` **and** one `adversarial-review-claude{1..4}`;
  **and** both `passed-review-codex` + `passed-review-claude`; **and** the body
  sections Summary / Determinism / Linux Semantics / Validation / Human Review
  Required (+ Relationship to gVisor for KVM).
- A PR **without** the label: *"passes unconditionally; this lint never
  second-guesses whether the label should have been applied"* (lines 22-23,
  67-70).

**Consequence:** dual review is machine-required for the 4 triggers only.
**Ordinary PRs have ZERO mandatory review enforcement today.** That is precisely
the hole the owner wants closed.

Labels that actually exist on `rrnewton/hermit` (verified via `gh label list`):
`post-facto-human-review`, `pre-land-human-review` (notional), `human-approved`
(owner-only), `adversarial-review-claude1/claude2/codex1`, `passed-review-claude`,
`passed-review-codex`, `locally-validated`. **No `human-review` label and no
`post-facto-review` label exist** — both are correctly obsolete/absent.

### 1b. The divergence — "when is dual review mandatory?" has two answers

| Source | Mandatory dual review applies to | Consistent with lint? |
| --- | --- | --- |
| `hermit/scripts/core-review-protocol-lint.sh` (ENFORCED) | any `post-facto-human-review` PR = **all 4 triggers** | — (this is ground truth) |
| `hermit/CLAUDE.md`/AGENTS.md (dual-approval labels enforced by the gate) | **all 4 triggers** | ✅ |
| PARENT `.claude/skills/post-facto-review.md` §2 (L62-67) | **all 4 triggers** ("Every PR carrying `post-facto-human-review` requires two independent adversarial reviews") | ✅ |
| PARENT `.claude/skills/backend-reality-reviewer.md` (L20-21) | defers to post-facto-review | ✅ |
| HERMIT `.claude/skills/post-facto-review/SKILL.md` §2 (L108-121) | **triggers 3 & 4 only** ("triggers 3 and 4, and any clock/time-virtualization change"); triggers 1 & 2 keep "the standard single-reviewer bar" | ❌ contradicts the lint it cites |
| HERMIT `.claude/skills/backend-reality-reviewer/SKILL.md` (L50-53) | **triggers 3 & 4 only** | ❌ |

So a **new-syscall PR (trigger 1)** or a **Reverie API change (trigger 2)**:
- Parent skill + lint + AGENTS.md say **dual review required**.
- Hermit skill copies say **single review is enough**.

Agents obey whichever copy their repo surfaces (hermit agents surface the hermit
`SKILL.md`, which is even out of step with hermit's own lint and AGENTS.md). This
is the blocking contradiction.

### 1c. Why they drift

`hermit/.claude/skills/{post-facto-review,backend-reality-reviewer}/` are real
directories, **not symlinks** (verified). The parent flat skills are generated
1-1 from ORC source memories (`$MEMDIR/{post-facto-review,backend-reality-reviewer}.md`,
`core_memory: true`, `core_skill:` path) via `scripts/sync-memory-skill.rs`;
`scripts/lint-memory-skill-sync.rs` enforces the 1-1 mapping and forbids nested
skill directories in the parent. There is **no channel** keeping the hermit copy
and the parent copy in agreement — they were hand-edited independently and drifted.

### 1d. The obsolete-label / stale-reference items

- Neither skill actually *instructs adding* `post-facto-review`/`human-review`
  labels; both say they "must not be used/recreated." That guidance is correct
  (the labels don't exist). **The stale item is the `human-review-first` cross-
  reference**: the HERMIT `post-facto-review/SKILL.md` links `human-review-first`
  as a live "dormant alternative" (L24, L29, L152, L175-177), and
  `hermit/.claude/skills/human-review-first/SKILL.md` still ships as an active,
  discoverable skill. In the PARENT this mode is **archived**
  (`.claude/archived_skills/human-review-first/SKILL.md`, excluded from discovery
  per `.claude/skills/README.md`). So hermit presents an activation path the
  parent has retired. That is the "stale human-review reference" to fix.

---

## 2. Target policy (what to encode)

1. **Baseline: exactly one adversarial review is mandatory on EVERY PR.** The
   reviewer is not the author; if the author is a claude-family model, the single
   reviewer is a codex-family model (or a distinct instance), and vice versa. The
   review is bound to the exact head SHA. Record it with one
   `adversarial-review-{claude|codex}N` round label + one `passed-review-{claude|codex}`
   at approval. A head change invalidates the passed label; re-review the new head.
2. **Escalation: DUAL (claude + codex) review is mandatory for the four core-change
   triggers** — (1) new syscall support, (2) Reverie API / core-abstraction
   change, (3) new determinization strategy, (4) core DetCore scheduling change.
   These are exactly the PRs that carry `post-facto-human-review`, so the existing
   lint continues to enforce the dual gate for them unchanged.
3. **Delete the divergence.** One canonical skill body; the other location becomes
   a thin explicit-link pointer. No second full copy to drift.
4. **Reviewer's mandate is central and generalized** (see §4).
5. **Preserve** label semantics and the comment-triage protocol (§5), and fix the
   stale `human-review-first` reference (§6).

### The one decision the owner must ratify: canonical location

**Recommendation: the canonical review-protocol skill lives in HERMIT**
(`hermit/.claude/skills/post-facto-review/SKILL.md`), and the parent points to it.

Rationale (decisive): the enforcement lint
(`hermit/scripts/core-review-protocol-lint.sh`), the PRs, and `hermit/AGENTS.md`
all live in hermit and must stay self-consistent for **standalone clones of
`rrnewton/hermit` and its CI**, which cannot assume the parent workspace exists.
Hermit impl/reviewer agents must have the protocol self-contained. A link from the
parent *into* `hermit/…` resolves (hermit/ is always a workspace subdirectory);
a link from hermit *out to* the parent does not resolve in a standalone hermit
clone. So canonical must be hermit; the parent is the one-way pointer.

Cost to accept: this inverts the current parent-README stance ("coordinator skills
stay in the parent"). For the two shared reviewer skills that also govern hermit
CI, that stance should yield — they are whole-repo landing disciplines with
in-hermit enforcement, not parent-only coordinator lore. The parent memories stay
(to satisfy the 1-1 memory↔skill lint) but their bodies shrink to a pointer plus
the coordinator's land-time-only duties.

(Alternative, if the owner prefers to keep canonical in the parent: hermit would
still need a self-contained copy for standalone CI, so the drift risk returns
unless a sync check is added. The recommendation avoids that.)

---

## 3. Proposed canonical skill body (hermit `post-facto-review/SKILL.md`)

Replace the hermit `SKILL.md` body with the following. Changes from today:
adds the universal single-review floor (§2A); reframes dual review as escalation
for the 4 triggers (§2B) so it matches the lint + AGENTS.md and drops the
incorrect "3 & 4 only"; adds the reviewer's mandate (§0); codifies label + comment
protocol (§5); removes `human-review-first` as a live activation path.

```markdown
---
name: post-facto-review
description: "Autonomous landing discipline: one adversarial review on EVERY PR, dual claude+codex review for the four core-change triggers, land on CI-green, human reviews after the fact."
---

# Post-Facto-Review Mode

The currently-active landing discipline. Changes land as soon as required
adversarial review is resolved and the authoritative CI gate is green; the human
reviews after landing and corrections fix forward. There is no active pre-land
human-approval mode.

## 0. The reviewer's mandate: PROVE THE CLAIM, DO NOT CONFIRM IT

The failure mode this protocol exists to stop is not missing reviews — it is
reviews that accept the author's framing. We have shipped many "done" claims that
were not. Every adversarial reviewer, on every PR, operates as a skeptic whose
job is to REFUTE the change, not to bless it:

- **Demand evidence.** A claim counts only with the exact command, its literal
  output, the head SHA it ran at, and measured numbers. "Passes", "works",
  "deterministic", "done" with no reproducible evidence at the PR head is a
  finding, not a result.
- **Re-run, don't re-read.** Confirm the cited tests exist and were run at the
  exact head SHA; where feasible, reproduce them. Type names, crate names, and a
  clean process exit are not integration.
- **A MOVED GOALPOST IS ITSELF A FINDING.** If the definition of done, the
  assurance level, the backend, the corpus, or the passing count silently changed
  between the task and the PR — or a test was narrowed, `#[ignore]`d, masked, or
  deleted to make the tree green — file it as a defect. Do not accept the new,
  easier goal.
- **Name the level precisely.** L0-L4, exact backend (ptrace/DBI/KVM), exact
  test/command with flags, and every relaxation. One backend passing is not a
  project-wide claim.

This posture is the generalized form of `backend-reality-reviewer`; that skill is
the backend-specific instance of the same anti-fakery stance and its deep
code-path audit applies whenever a backend claim is under review.

## 1. PR comment convention and comment-triage protocol

Every PR description and comment created under this workflow MUST start with the
role tag: `[impl agent, MODEL]`, `[adversarial-reviewer agent, MODEL]`,
`[coordinator, MODEL]`, or `[Human]`.

Reviewer/author comment discipline (mandatory; zero unaddressed at land):

- **Every review comment is triaged to a task** before it is considered handled.
- **A thumbs-up/👍 reaction is applied only after** the comment has been filed to
  a task or resolved with evidence — never as a substitute for addressing it.
- **The author responds `[impl agent, MODEL]`** to each comment, either citing the
  exact commit SHA that addresses it, or **refuting it with evidence** (command +
  output). "Fixed" without a commit reference is not a response.
- **Zero unaddressed review comments** may remain when the PR lands.

## 2. Mandatory adversarial review

### 2A. Baseline — one review on EVERY PR

Every PR requires **at least one** independent adversarial review bound to the
exact head SHA before landing, following the mandate in §0. The reviewer is never
the author; use a reviewer from a different model family than the author (or a
separate instance). Record the round with one `adversarial-review-{claude|codex}N`
label and the approval with the matching `passed-review-{claude|codex}` label. A
new push invalidates the passed label — re-review the new head.

### 2B. Escalation — DUAL claude+codex review for the four core-change triggers

A PR that meets any of the four triggers below carries `post-facto-human-review`
and must survive **two independent adversarial reviews before landing — one by a
claude-family reviewer and one by a codex-family reviewer** — over repeated
author-fix / reviewer-recheck rounds. Two model families reduce correlated blind
spots on exactly the changes where a subtle regression is costliest. This dual
gate is enforced by the `core-review-protocol` merge-gate
(`scripts/core-review-protocol-lint.sh`): it requires
`adversarial-review-codex{1..4}` + `adversarial-review-claude{1..4}` and current
`passed-review-codex` + `passed-review-claude`.

The four triggers (apply `post-facto-human-review` if and only if one holds):

1. **New syscall support** — verify `// AUTONOMOUS-BOT-IMPLEMENTED` at the new
   dispatch/classification entry and `// TODO-HUMAN-REVIEW(PR-id)` at the
   implementation/determinization block.
2. **A Reverie API or core-abstraction change** to the `Tool`, `Guest`,
   `Backend`, or syscall-interception model.
3. **A new or changed core determinization strategy** (not a routine
   implementation of an established one), including guest-visible virtual-time or
   clock semantics.
4. **A core DetCore scheduling change** — anything affecting how programs are
   scheduled, preempted, blocked, awakened, or explored during race search.
   Always labeled. PR #1151 is the canonical good example.

Routine backend-parity work toward the golden ptrace reference is NOT a trigger
by itself; it still gets the §2A single review. "Backend parity change" is not a
valid dual-review rationale.

## 3. Mandatory PR description sections

Summary; Determinism (logic/informal proof, not only tests — for time changes,
prove continuous fine-grained virtual time, not a first-sample match); Linux
Semantics; Validation (exact commands/outcomes/limitations; for time changes,
repeated + cross-exec/thread/backend reads); Relationship to gVisor (KVM only);
Human Review Required (only when labeled — name the numbered trigger).

## 4. Continuous virtual time is sacred

Treat any weakening of continuous, fine-grained guest virtual time as a landing
red flag (first-read-only fabrication, per-syscall/per-process resets, advance-on-
observation, host-wall-time derivation, single-sample "proof"). See
`continuous-virtual-time-is-sacred`. Such changes are trigger-3/4 core changes and
take the dual gate.

## 5. Labels

- `post-facto-human-review` — the single routing label for the four triggers;
  activates the dual gate in §2B. Never withheld to dodge the gate.
- `adversarial-review-{claude,codex}N` — append-only round audit trail ("a review
  round happened"); not approval.
- `passed-review-{claude,codex}` — exact-head approval; invalidated by any new push.
- `human-approved` — owner-only; never self-apply, remove, or alter.
- `pre-land-human-review` — notional opposite; never applied in this mode.
- `locally-validated` — only when local evidence proves a residual CI failure is
  baseline/environmental.
- The obsolete `human-review` and `post-facto-review` labels must not be recreated
  or applied.

## 6. New-syscall code markers

`// AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification entry and
`// TODO-HUMAN-REVIEW(PR-id)` at the implementation/determinization block. Verify
both before labeling/landing trigger 1; keep them at the smallest new-syscall
regions only.

## 7. Land when review and CI are green

Once the required review (single per §2A, or dual per §2B) is resolved and the
authoritative gate is green (Hermit: GitHub-hosted `Regular tests`; treat known-
environmental self-hosted failures per repo policy, never bypass a genuine product
failure), land the authorized change without waiting for a human. For a labeled
PR, verify the required sections, both numbered review trails, and both exact-head
passed labels first. Squash-merge, record the exact merge SHA, rebase dependents
in dependency order.

## 8. Human reviews after landing

The human reviews landed work after the fact; corrections fix forward via follow-up
PRs. Remove a `// TODO-HUMAN-REVIEW` marker only when its concern is addressed.

## Task-closure gate for KEY API changes

For a KEY Reverie API / core-abstraction change (`Tool`/`Guest`/`Backend`/
interception), loudly report the change and its implications to the owner and do
not CLOSE the task until the owner has discussed it. This is a closure gate, not a
pre-land gate — post-facto landing still applies after review + CI green.
```

---

## 4. Proposed changes to the other files

### 4a. PARENT `post-facto-review` (source memory → regenerated skill)

Because the parent skill is generated from `$MEMDIR/post-facto-review.md`, edit
the **memory** and re-run `scripts/sync-memory-skill.rs`; do not hand-edit the
flat skill. New memory body = a pointer + coordinator land-time duties:

- One-line: "Canonical protocol: `hermit/.claude/skills/post-facto-review/SKILL.md`
  (co-located with its `core-review-protocol` lint). This memory carries only the
  coordinator's land-time responsibilities; all trigger/label/review-count rules
  live in the canonical hermit skill."
- Keep: coordinator applies labels + lands only after §2A/§2B review is resolved
  and the authoritative gate is green at the exact head; never apply
  `pre-land-human-review`; never alter `human-approved`.
- Update the `description` frontmatter to match the new policy (one review every
  PR; dual for the four triggers).
- Run `scripts/sync-memory-skill.rs` then `scripts/lint-memory-skill-sync.rs`.

### 4b. HERMIT + PARENT `backend-reality-reviewer` — generalize the posture

- HERMIT `backend-reality-reviewer/SKILL.md`: **delete** the "3 and 4 only"
  sentence (L50-53) and replace with a one-line pointer to canonical
  `post-facto-review` §0 + §2 for the review-count rule. Keep the entire deep
  code-path audit / scoring / report-format machinery — that is the backend-
  specific instance of the §0 mandate and stays.
- Add one framing line at the top: "This is the backend-specific instance of the
  reviewer's mandate (`post-facto-review` §0: prove the claim, do not confirm it;
  a moved goalpost is itself a finding). The same posture applies to every PR;
  this checklist is what 'prove it' means for a backend claim."
- PARENT `backend-reality-reviewer.md` (via its memory): keep as-is except update
  the review-count reference to point at the canonical hermit skill, and add the
  same one-line generalizing framing. Re-run the memory sync.

### 4c. Fix the stale `human-review-first` reference

- HERMIT `post-facto-review/SKILL.md`: drop the "dormant alternative" cross-links
  (already removed in the §3 rewrite above — it now says "There is no active
  pre-land human-approval mode").
- Recommend archiving the hermit `human-review-first` skill to match the parent
  (move `hermit/.claude/skills/human-review-first/` → an archived location
  excluded from discovery, or delete it), so hermit stops presenting a retired
  activation path. Owner to confirm, since it ships in `rrnewton/hermit`.

---

## 5. Enforcement gap (flag for a follow-up, owner's call)

The skill prose will mandate "one review on every PR," but the lint
(`core-review-protocol-lint.sh`) only gates **labeled** PRs; unlabeled PRs still
pass unconditionally. Prose alone is "an agent that forgets"; the lint header
itself notes the protocol failed once when it lived only in skills (PR #1095).

**Recommended follow-up task** (separate PR, not this analysis): extend the
merge-gate so **every** PR requires at least one `adversarial-review-{claude|codex}N`
+ one matching `passed-review-{claude|codex}` at the head SHA, and labeled PRs
additionally require the dual set. That makes the universal single-review floor
"code that never forgets" instead of an honor-system rule. Left as a proposal
because the owner said he will update the skills himself and did not authorize a
lint/CI change under this task.

---

## 6. Files to change (summary for the owner)

| File | Change |
| --- | --- |
| `hermit/.claude/skills/post-facto-review/SKILL.md` | **Canonical rewrite** (§3 above): one review every PR + dual for 4 triggers; add reviewer mandate §0; codify comment protocol; drop human-review-first activation path. |
| `$MEMDIR/post-facto-review.md` (→ parent flat skill via sync) | Shrink to pointer + coordinator land-time duties; update description; re-sync. |
| `hermit/.claude/skills/backend-reality-reviewer/SKILL.md` | Delete "3 & 4 only"; add generalizing framing line; point review-count at canonical. |
| `$MEMDIR/backend-reality-reviewer.md` (→ parent flat skill via sync) | Same framing + point review-count at canonical; re-sync. |
| `hermit/.claude/skills/human-review-first/SKILL.md` | Archive/remove to match parent (owner confirm). |
| `hermit/scripts/core-review-protocol-lint.sh` | (Follow-up only) universal single-review gate — see §5. |

Unchanged: the four triggers themselves; the dual-gate lint behavior for labeled
PRs; PR description sections; new-syscall code markers; `human-approved` owner-only
rule; continuous-virtual-time policy.
```
