# Owner decision queue — consolidated for re-engagement

Compiled 2026-08-06 by `hermit-cc` (coordinator) · task `consolidate-owner-decision-queue-for-re-engagement`

Every item below was **re-verified against the live system today**, not copied forward. Three items in the
inbound list did not survive that check — see *Stale / already resolved* at the bottom. Each live item is:
**what** · **why it needs you** · **recommended option**.

---

## 0. THE ONE THAT BLOCKS EVERYTHING ELSE

### VALIDATE BOX WEDGE — the box-exclusive validate lease is quarantined and the tooling cannot self-clear it

**What.** `ci-hub validate-lock status` reports `QUARANTINED (published domain lacks final census)`, left by
agent `hermit-w5` (`operation=validate:f792cf1c…`, `pgid=3598395`, `owner_pid=3594054`). The recorded reason is
*"published payload ended without a complete residual census; same-boot absence cannot exclude an escaped
descendant."*

**Verified, not inferred.** The owner process is gone by four independent signals: no processes in pgid
3598395, owner pid absent, **zero `safe-ci-*` cgroups anywhere** under `/sys/fs/cgroup`, no active validate
systemd units. Boot id is unchanged. The sanctioned remedy `validate-lock reclaim-dead` **refuses**, and
reading the guard shows why it always will: `validate_lock.rs:1155-1195` evaluates `cleanup_verification()`
*before* `owner_liveness()`, and `Uncensused` returns `ReclaimNotProven` without ever reaching the liveness
check. `Uncensused` is also a **distinct state from `Recoverable`** (`validate_lock.rs:1092-1099`), and only
`Recoverable` is what `reclaim-dead` can clear. Further, `try_acquire()` calls `require_no_cleanup()` as its
first action (`:941`), so **every** acquire is hard-refused before it can even enter the FIFO queue.

**Why it needs you.** This is not a slow queue, it is a closed door, and it is closed for **every agent on the
box**, not one task. No head can mint a validate receipt, so nothing can satisfy the ledger leg of the merge
gate, so **nothing can land at all**. Clearing it means either a reboot (a different boot id makes absence
conclusive) or someone with authority adjudicating the census gap. Both are outside what an agent should
decide: a reboot destroys in-flight work across ~18 agents, and hand-deleting the lock files would defeat a
fail-closed safety gate whose entire purpose is to refuse when it cannot prove no escaped descendant is still
consuming the machine.

**Recommended.** Adjudicate rather than reboot if you can: confirm out-of-band that no `hermit-w5` descendant
survives, then clear the cleanup record with authority. Reboot is the blunt fallback and is conclusive. Do
**not** let an agent hand-delete the lock — if the census gap is real, the next run comes back contended and
you get another `NEEDS-RERUN`, which is the symptom we are already stuck in.

---

## 1. Landing-path decisions

| # | Item | Why it needs you | Recommended |
|---|---|---|---|
| 1.1 | **Landing SPOF** — hosted `ubuntu-latest` lane is the single point of failure for all landing (`hosted_ubuntu_latest_lane`, BACKLOG) | Architectural: accepting the SPOF vs funding a second lane is a cost/risk call | Add a second lane; the current wedge shows what one lane costs |
| 1.2 | **Chaos seed count** — durable seed list is a coverage/runtime tradeoff (`chaos_seed_lists_are`, IN_PROGRESS) | You own the coverage-vs-minutes tradeoff | **Largely overtaken by events**: PR #1750 widens `seeds=[0,9]` → `0..31` and its validate showed the target cell GREEN (`chaos distinct=2 passes=20 failures=12`). The live decision is now "land #1750", not "pick a number" |
| 1.3 | **Merge gate trusts label presence, not the ledger** (`merge-gate-trusts-label-presence-not-the-ledger`, BACKLOG) | P0 fake-green class: the sole required check can pass on a label rather than evidence | Note carries a `CLOSURE-VERIFIED` record yet status is BACKLOG — **confirm whether this is already fixed and merely mis-statused** before spending on it |
| 1.4 | **Collapse the 10 one-fixture PRs into one stack?** (`decide-collapse-10-one-fixture-prs`, BACKLOG) | It would close other agents' PRs — a social/ownership call, not a technical one | Decide only after the wedge clears; collapsing now buys nothing while nothing can land |

## 2. Product / semantics decisions (each freezes a contract)

| # | Item | Why it needs you | Recommended |
|---|---|---|---|
| 2.1 | **`times(2)` vs `getrusage` disagree on system time** (`owner-decision-times2-vs-getrusage-continuity` + `times_2_tms_stime`, BACKLOG) | The byte-parity fix **freezes a class** — three options, all with lasting semantics | Needs your ruling; measurement is complete incl. the KVM gap |
| 2.2 | **`__vdso_getrandom`: `-ENOSYS` vs determinized bytes** (`decide-vdso-getrandom-enosys-vs-determinized-bytes`, BACKLOG) | ENOSYS works today but is a permanent capability statement | Determinized bytes is the strategically right answer; ENOSYS is the cheap one |
| 2.3 | **DBI anon-mmap divergence** (`root-cause-dbi-anon-mmap-divergence-inherent-or-fixable` + `disposition-dbi-anon-mmap-cell-not-parity-achievable`, both IN_PROGRESS) | Declaring a cell NOT-PARITY-ACHIEVABLE is a permanent scorecard concession | Confirm the DynamoRIO limit is inherent before conceding the cell |
| 2.4 | **Zero-ptracer requires a Reverie core-abstraction change** | Reverie API Policy explicitly requires discussing core-abstraction changes with you | The referenced task id does not resolve — see *Broken references* |

## 3. Fleet / infrastructure

| # | Item | Why it needs you | Recommended |
|---|---|---|---|
| 3.1 | **Codex reviewer not spawnable** — blocks dual review for every core-change PR (#1147, flock #1742). Live task: `spawn-conflates-cli-type-with-model-selection-claude-got` (IN_PROGRESS) — the spawn harness conflates CLI-type with model-selection | Only you can relaunch with a working codex identity | Relaunch; until then every core-change PR is review-blocked regardless of CI |
| 3.2 | **Reverie pin auto-safe-bump** (`reverie-pin-auto-safe-bump-hermeticity`, IN_PROGRESS) | Changes how pins move — a policy decision, not an implementation detail | Approve the model before it is implemented |
| 3.3 | **Test power-to-weight** (`epic-test-powerweight` OPEN, `test-powerweight-audit-slowest-first` BACKLOG) | Verdict was "costs justified, no cheap speedup" — accepting that is a budget call | Accept and close, unless the wedge changes the calculus |
| 3.4 | **Slot pool is far over policy cap** — **85 slot directories** against a documented cap of 12 active | Nobody owns this; it is why a feasibility test cannot cheaply get a slot | Mass-park/reclaim, or raise the cap and say so |

## 4. Drain residue awaiting a yes/no (7 tasks, all BACKLOG, owner cleared)

From the stranded-work triage. Each carries the specific question on its task.

- **4 unpushed** — commit exists in no remote branch: `flag-combination-matrix-coverage`,
  `liteinst-close-remaining-cells`, `sig-alarm-e9patch-exceeds-wall`, `signal-delivery-determinism`.
  *Re-push from the owning slot, re-implement, or drop?*
- **2 rescue-only** — commit lives only inside `rescue/`/`archive/` branches that are 17–22 ahead and 133
  behind across 56–73 files: `gitignore-star-log-silently-excludes-golden-logs`,
  `restate-headline-numbers-with-provenance`. *Authorize a focused cherry-pick, or drop?*
- **1 not landable** — `prepare-stacks-for-landing`; its own commit message says *"the workspace does not
  compile."* *Re-implement or drop?*

Plus **3 reverie branches carrying no unique content** (merge + empty commits only), listed not deleted:
`codex/sabre-simd-hermit-pin`, `drain/reverie-221`, `impl-kvm-example-tools-matrix-slot324`. *Delete?* — an
agent should not delete branches on an ambiguous yes.

---

## Stale / already resolved — do not spend time on these

| Item as reported | Verified today | Disposition |
|---|---|---|
| **Egress allowlist blocks github.com and crates.io** | `with-proxy git ls-remote github.com` → **rc=0**; `with-proxy gh api user` → **rc=0**; `crates.io` → **HTTP 403** | **Half stale.** github + gh are reachable. Narrow the item to **crates.io only** |
| **libunwind missing box-wide; every hermit binary fails at startup** | 6 libunwind entries in `ldconfig`; `/usr/lib64/libunwind.so.8` present; `hermit --version` → **rc=0**; `ldd` resolves both libunwind sonames | **Resolved.** Close `libunwind-missing-fleet-blocker` |
| **Chaos seed-count decision** | PR #1750 already widens the list and its target cell validates green | **Overtaken** — see 1.2 |

## Broken references found while compiling this

`owner-decision-zero-ptracer-requires-reverie-core-abstractio` **does not resolve** as a task id. This is the
same class as the dangling successor slugs repaired earlier today: tg derives `local_id` from the **title**,
lowercasing and **truncating to four words**, so any longer slug written into a comment or doc is unreachable
by construction. Anything citing that id is currently pointing at nothing.
