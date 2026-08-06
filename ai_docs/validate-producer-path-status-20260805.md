# `systemd-run --user` as the validate producer path — status and a correction

**Task:** `systemd-run-user-is-the-validate-producer-path` (P0)
**Date:** 2026-08-05
**Bound to:** dev-hermit parent (`ci-hub/`), hermit `b64d893a`
**Mode:** local read + verification. No validate launched, no egress, nothing mutated.

---

## Two findings, and the second one matters more

**1. The entrypoint the task asks for already exists, is committed, and is complete.**

**2. The literal invocation in the dispatch is the anti-pattern it was built to replace.** Wiring it
as a reusable script would mass-produce *unqualified* ledger records across the ~105-PR drain —
exactly the "permanent wall of fake reds" this task's own description warns against.

---

## The existing entrypoint

`ci-hub validate-run` → `ci-hub/validate/start_unit.py` (268 lines, **tracked and committed**;
landed in `aa36f2d` *"Route validation through admitted user units"*, refined by `0844c42` *"Make
rebase-before-validate mechanical"*). Its own help: *"Launch a detached validation whose service
enters through validate-lock."*

It already does everything the dispatch specified:

| Dispatch requirement | Where it lives in `start_unit.py` |
|---|---|
| `systemd-run --user --unit=<n>` | `:133` |
| `--working-directory=<worktree>` | `:137` |
| `--setenv HOME=` / `--setenv PATH=` | `:139-142` |
| durable log surviving agent recycle | `:146-148` `StandardOutput=append:` / `StandardError=append:`, default `ignored/validate/<unit>.log` (`:217`) |
| detached / monitorable | transient user unit; `systemctl --user show <unit>` |
| **anchor + checkout preflight** | `:90-92` → `preflight_validate.py` |
| **admission through the lock** | `:150` the unit execs `validate-lock`; emits `"admission": "ci-hub validate-lock"` (`:249`) |
| queue/lease bounds | `--wait` (default 7200), `--hold` (default 1200) at `:182-183` |

**The `cargo: command not found` bug is fixed and guarded.** The 2026-08-04 05:04 failure (full leg
died in 2 s because the user scope did not inherit `PATH`, so `/home/newton/.cargo/bin` was absent)
cannot silently recur: `:119-122` raises *"HOME and PATH must be set so cargo/rustup resolve inside
the user unit"* before launching anything.

## Why the dispatch's raw recipe must not be wired up

The dispatch specifies:

```bash
systemd-run --user --unit=<n> --working-directory=<wt> ... \
  bash -c 'PR_NUMBER=<n> with-proxy ./validate.sh > <durable-log>'
```

That omits the lock, and `validate.sh` treats the lock as the **proof of exclusivity** —
`validate.sh:1345-1361`:

```bash
# Prove that this shell is a descendant of the live process-bound validate-lock
# owner. Merely setting an environment variable is not enough: the owner sidecar
# must name the same PID, and that PID must occur in this process's ancestry.
function validate_lock_exclusivity_proven { ... }
```

and `validate.sh:1403-1412`, inside `append_validation_ledger`:

```bash
elif validate_lock_exclusivity_proven; then
    concurrent_validates_json=0
    concurrency_proof_json='"validate_lock_owner_ancestry"'
else
    # A bare run with no observed peer is UNKNOWN, not proven exclusive.
    concurrent_validates_json=null
    concurrency_proof_json=null
fi
```

**A bare `systemd-run` of `validate.sh` still writes a ledger record — but with
`concurrent_validates = null` and `concurrency_proof = null`.** The run is boxed and it executes;
what it *cannot* do is state that it ran solo.

That is precisely the failure this task exists to prevent. Its own description says a red produced
under contention is *"worse than no data, because it looks like data — and unlike a gap, it does not
invite re-measurement."* A record whose `concurrency_proof` is `null` is indistinguishable, after
the fact, between "the product failed" and "34 peers were running." Running the ~105-PR drain
through the raw recipe would mint 105 such records into the artifact we treat as the source of
truth.

`ci-hub validate-run` is not bureaucracy around `systemd-run` — the lock ancestry is *the thing that
makes the receipt mean something*. The 2026-08-05 03:53 note recorded the same trap independently:
*"a bare systemd-run of validate.sh without the lock produces NO trustworthy record."*

**So the correct producer command is:**

```bash
./ci-hub/ci-hub validate-run --checkout <worktree> --agent <agent> \
  --target <exact-40-hex-head> --pr <number> -- full
```

This matches `CLAUDE.md`, which already names `ci-hub validate-run` as *"the sole admission point."*

## Nothing was built

No new script was written, deliberately. The owner's standing rule on this subsystem is *"DO NOT
BUILD SIX SEPARATE MECHANISMS… a second implementation is the drift bug we keep finding — the `-j`
default defined at two lines, the lint guarding 3 of 6 backends, the ledger path under three
names."* A second producer entrypoint alongside `validate-run` would be that bug, and the specific
harm is concrete: the duplicate would be the one *without* lock ancestry.

## Residual gap: the libunwind workaround is encoded nowhere

The dispatch asked me to note the libunwind `/tmp/lu` workaround. **It is not represented in any
tooling**, verified by search:

- `scripts/install-deps.sh:33,42` installs `libunwind-dev` / `libunwind-devel` **system-wide** — the
  path that needs `sudo`.
- `scripts/doctor.sh:90` *checks* for `libunwind-ptrace` but does not remediate.
- **Zero hits for `/tmp/lu`** anywhere in `ci-hub/`, `scripts/`, or `hermit/`.

So the local-prefix workaround (building/installing libunwind under `/tmp/lu` and pointing
`PKG_CONFIG_PATH`/`LD_LIBRARY_PATH` at it, for hosts where the system package is absent and sudo is
unavailable) exists only as tribal knowledge. On a host missing it, a validate launched through the
correct producer path still fails at build time, and the ledger will record a **product-looking
red** for an environment cause.

**Recommended fix, one place:** add a libunwind availability check to
`ci-hub/validate/preflight_validate.py` — the same file `start_unit.py:90-92` already calls. It
should refuse to launch (or emit a named env-fault) rather than let the run mint a misleading red.
That keeps it in the single preflight rather than adding a seventh mechanism.

*Not implemented here:* `preflight_validate.py` is a parent file this task does not explicitly name,
another agent has `ci-hub/validate/tests/` modifications in flight, and with egress down the change
could not be published or validated. Flagged rather than edited.

## What actually blocks the ~105-PR drain

Not the producer path — that is built and correct. The blockers are:

1. **Egress.** Every drain step needs GitHub: rebase onto current main, push, read check state, land.
   Refused all session (`api.github.com not allowlisted for agent_id agent:claude_code`).
2. **Serialisation, not concurrency.** The task's own sequencing stands and is unchanged by anything
   here: solo run at moderate `-j` → *derive* safe concurrency from that run's measured footprint →
   only then batch. The earlier `~35` ceiling was derived from a **portable** run (7.1 GiB peak, 0
   OOM kills) and explicitly *does not transfer to full runs*, which OOM at per-step caps under high
   `-j`. Do not reuse it as a constant.
3. **`detcore_misc`.** Passes 23/23 standalone, hangs probabilistically at 16-wide. Concurrency does
   not merely slow the drain — it triggers the defect that makes runs fail.

## Provenance

| Claim | Source | Status |
|---|---|---|
| `validate-run` → `start_unit.py`; tracked; commits `aa36f2d`, `0844c42` | `ci-hub/ci-hub.rs:1642`, `git log`, `git ls-files` | **verified this session** |
| Every dispatch requirement present, with line numbers | `ci-hub/validate/start_unit.py` | **read this session** |
| HOME/PATH guard at `:119-122` | same | **read this session** |
| Bare run ⇒ `concurrency_proof = null` | `hermit/validate.sh:1345-1361, 1403-1412` @ `b64d893a` | **read this session** |
| libunwind `/tmp/lu` absent from all tooling | search over `ci-hub/`, `scripts/`, `hermit/` | **verified this session** |
| `~35` ceiling portable-only; `detcore_misc` 16-wide hang; PATH-bug history | task notes, 2026-08-04 | inherited; **not re-measured** |
