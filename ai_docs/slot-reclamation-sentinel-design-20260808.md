# Slot reclamation: the sentinel design — 2026-08-08

**For the owner.** This was commissioned as *"design a sentinel, then bring it to the
owner, because a long-lived privileged process on a shared box is an
infrastructure decision."*

**The headline is that you do not have that decision to make.** The component is
not missing, it is not long-lived, and it is not privileged. It exists, it is
unprivileged, it is transient, and it has already run. What is missing is
something much smaller, and it is named in §4.

---

## 1. The premise, corrected

| Claim as commissioned | Measured |
|---|---|
| A component is missing and no authorization can create it | `scripts/codex-slot-sentinel.rs` exists — 33,740 bytes, executable, dated 2026-08-06 |
| It needs a long-lived process | Transient. One `systemd-run --user` unit per slot, which exits |
| It needs privilege | `--user` throughout. No root, no system units, no new daemon |
| It has never been exercised | It has. Slots `243` and `slot74` carry `codex-systemd-sentinel-v1` leases it created and revoked |

Interface, from the tool itself:

```
codex-slot-sentinel.rs plan   --slot SLOT --working-directory ABSOLUTE-PATH
codex-slot-sentinel.rs launch --plan-json JSON
codex-slot-sentinel.rs verify --lease-json JSON
codex-slot-sentinel.rs revoke --lease-json JSON [--recover]
codex-slot-sentinel.rs prove-revoked --lease-json JSON
```

> `revoke` stops only the exact recorded systemd incarnation.

## 2. Why a sentinel is the right shape, and why no agent can substitute

`release-worktree.rs --clean` requires the recorded owner to satisfy **both**:

- **have lease data** — a resolvable `tmux_pane_id` / cgroup (`:1050`); and
- **be provably dead at the moment of action** — the lease is resolved, the pane
  pid walked to its cgroup, subtree-population checked, and cleanup **refused** if
  the owner is alive (`:1431-1461`). That is Hard Invariant 16.

A dead predecessor has no lease data, so it fails the first. A live agent that
adopts the row supplies the lease and then fails the second. **An agent cannot
certify its own death**, so no agent can ever be the owner of a slot it reclaims.
The owner must be an entity whose death is externally provable — which is exactly
what a transient systemd unit is. The recorded death proof is
`active_state=inactive`, `load_state=not-found`, `cgroup_absent=true`,
`cgroup_members=[]`.

This is why the earlier framing — "grant coordinator sentinel authority" — could
not work. It was never a permission. But it also was not a missing component.

## 3. The refusals are already implemented, and I exercised them today

The commission asked that the constraint shape the design rather than be bolted
on. It already is. Every refusal below was triggered live, by me, today:

| Refusal | Status | Evidence |
|---|---|---|
| **Dirty slot** | Present | `--clean` refuses uncommitted work unless `--force`. `val1147` (3 staged files) held on exactly this |
| **Occupied slot** | Present, and **stronger than mine** | `243` refused: *"post-fence live process ownership … pid 702164 fd/187=…/target/debug/deps"* — **open file descriptors**, not just cwd |
| **Live owner** | Present | `lander` refused: *"recorded owner 'tickhub-surgeon' remains live in pane %82, cgroup …, members=1"* |
| **Undocumented work** | Present, and runs **first** | *"pre-recycle guardrail: verifying every outer and initialized nested HEAD is reachable from its own origin… ✓ verified 4 outer/nested repository HEAD(s) on origin"* — before any mutation |
| **No pattern/name matching** | Present | `revoke` "stops only the exact recorded systemd incarnation"; occupancy is fds and cgroup membership, never a process name |
| **Fail-closed on refusal** | Present | Every refusal above ended *"registry state retained"* — nothing partially removed |

So the three negatives the commission asked me to plant are already planted, and
two of them fired on real slots during this session rather than in a fixture. The
positive half (a clean abandoned slot reclaims unattended) is the half **not** yet
demonstrated end to end — correctly identified as the easy half that proves least.

## 4. What is actually missing: one race, and it is self-inflicted

Reclaiming `243` — clean, origin-contained, zero processes by cwd — failed like
this:

```
✓ verified 4 outer/nested repository HEAD(s) on origin
release-worktree: post-fence live process ownership below
  worktrees/243/.hermit.release-worktree-2250303-1786175206095754017:
  pid 702164 fd/187=…/target/debug/deps, fd/237=…/target/debug/.fingerprint
registry state retained
```

**pid 702164 is `watchman --foreground`, ppid=1, running 38 hours, cwd=`/`.** It is
not an agent and holds no work. It is a filesystem indexer that noticed the
quarantine directory *the release tool had just created* and opened descriptors
into it. By the time the post-fence check ran, watchman was inside the tool's own
scratch path.

**The tool trips its own occupancy check by performing the operation.** Measured
after the fact: `fds into worktrees/243 = 0` — watchman had already let go. This is
a race, not an occupied slot, and it is the single thing standing between the
current state and unattended reclamation.

Three candidate fixes, in preference order:

1. **Scope the post-fence check to the pre-existing slot content, not to the
   tool's own quarantine path.** The quarantine directory did not exist a second
   earlier; nothing legitimate can be "using" it. Narrowest fix, no policy change.
2. **Re-poll with a short bounded backoff before refusing.** Watchman releases on
   its own. Weaker — it trades a deterministic check for a timing assumption.
3. **Exclude non-agent infrastructure by cgroup**, i.e. ignore fd holders outside
   the agent slice. Most general, most likely to erode the property; not
   recommended without a much stronger argument.

Recommendation: (1). It preserves fail-closed behaviour exactly and removes only a
window the tool itself creates.

## 5. A weakness in my own gate, found by this

`ci-hub/health/slot_disk_residue.py` determines occupancy from `/proc/<pid>/cwd`
**only**. `release-worktree.rs` also checks **open file descriptors**, which is
strictly stronger: a process can hold a slot's build artifacts open with its cwd
elsewhere — precisely what watchman did here. My gate is detect-only so the
consequence is a false "reclaimable", not a deletion; but the docstring currently
claims cwd is *the* unforgeable occupancy signal, and that overstates it. It
should either adopt the fd check or state the limitation. Filed separately; not
changed here, because this task is a design.

## 6. What to run, once §4 lands

No new infrastructure. Per slot, unattended:

1. `allocate-worktree.rs --recover-legacy-unbound-owner --codex-systemd-sentinel
   --recovery-note "<reason>"` — installs a transient sentinel as owner,
   preserving the historical owner evidence and never rebinding the caller.
2. `codex-slot-sentinel.rs revoke --lease-json …` — stops that exact incarnation
   and journals the death proof.
3. `release-worktree.rs --slot <slot> --clean` — the owner is now provably dead,
   so gates 3 and 4 are both satisfied and every refusal in §3 still applies.

`243` is the cheapest end-to-end proof: 35 GB, clean, origin-contained, sentinel
already revoked, and it now fails only on §4. `slot74` must **not** be included —
it has a parked herdr terminal tab (pid 3448759, herdr-server child, 38 h,
00:00:00 CPU) with cwd inside it, so it reads occupied and should stay that way
until someone closes the tab. Do not signal that pid; it is not ours.

## 7. The decision actually in front of the owner

Not "should we run a privileged daemon" — nothing here is privileged or
long-lived. It is narrower:

- **Approve fix (1) in §4**, a scoping change to one occupancy check; and
- **authorise unattended reclamation** of clean, origin-contained, unoccupied
  slots through the sentinel path, with the §3 refusals binding and `--force`
  remaining prohibited by the settled val1147 policy.

Every destructive step remains gated by proofs that already exist and already
refuse. The residual risk is not a missing safeguard; it is that a safeguard is
currently *too* eager, and refuses a slot the tool itself disturbed.
