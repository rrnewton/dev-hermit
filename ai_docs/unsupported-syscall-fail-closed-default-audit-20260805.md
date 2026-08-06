# Standing directive: make unsupported-syscall panic the default — status audit

**Task:** `make_unsupported_syscall_panic` (P1)
**Date:** 2026-08-05
**Bound to:** hermit main `b64d893ae9ea6404472eae9cb86102d91ec642ef`
**Mode:** local read only — egress refused all session, nothing pushed, no branch mutated

---

## Answer in one line

**The directive is NOT implemented — the ordinary-run default is still fail-open. But its
documented blocker is GONE, the true blast radius is 1 test (not 20), and a complete
implementation already exists on a branch. This is now a rebase-and-land, not a design problem.**

---

## 1. Current default: fail-OPEN (confirmed, not assumed)

`detcore-model/src/config.rs:330-332` — a bare `#[clap(long)]` bool, no `default_value`:

```rust
/// Fail immediately on unsupported syscalls instead of forwarding them.
/// Explicit strict mode enables this policy.
#[clap(long)]
pub panic_on_unsupported_syscalls: bool,
```

It is set true by exactly three things, all opt-in:

| Trigger | Site |
|---|---|
| `--strict` | `run.rs:1996-1998` |
| `--panic-on-unsupported-syscalls` | the flag itself |
| `HERMIT_FAIL_CLOSED=1` | `run.rs:3058-3060` (`effective_det_config`) |

And main **actively asserts the fail-open default** —
`run.rs:1082` `strict_flag_preserves_deterministic_defaults_and_rejects_unsupported_syscalls`:

```rust
let mut normal = RunOpts::parse_from(["fakehermit", "fakeprog"]);
normal.validate_args_with_perf_support(true).unwrap();
assert!(!normal.det_opts.det_config.panic_on_unsupported_syscalls);   // :1085
```

No `--allow-unsupported-syscalls` opt-out exists anywhere on main. **Directive unimplemented.**

## 2. The documented blocker is GONE — this is the finding that changes the decision

`ai_docs/syscall-coverage-map.md` is the reason this directive stalled. It argued the flag was
*untrustworthy in principle*:

> "Release `hermit run` subscribes to 78 syscall numbers… In an optimized run, an unsubscribed
> syscall never reaches `handle_syscall_event`… Consequently `--panic-on-unsupported-syscalls`
> does not detect the 291 missing release entries."
>
> Recommended plan, item 1: "**A release strict run must subscribe to all syscalls (or install an
> equivalent deny filter) before `panic_on_unsupported_syscalls` can be trusted.**"

**That prerequisite has since been met.** `detcore/src/lib.rs:797-807` at `b64d893a`:

```rust
fn subscriptions(config: &Config) -> Subscription {
    ...
    if !config.passthru_opt {
        // Fail closed by default in every build profile. Besides allowing syscall-specific
        // handlers to run, interception is what charges generic syscall logical time.
        Subscription::all()
    } else {
        // Explicit performance opt-in: unlisted syscalls bypass Detcore entirely. Keep this
        // path separate so its allow-list can be tightened without weakening the default.
        let mut subscription = Subscription::none();
        ...
    }
}
```

The 78-syscall allowlist is now gated behind `passthru_opt`, an explicit performance opt-in. The
default subscribes to **everything, in every build profile**. So an unsupported syscall now *does*
reach the check, and the flag would mean what it says.

Corroborating: `run.rs:1999-2004` already refuses `--passthru-opt` combined with fail-closed —
the two modes are recognized as mutually exclusive.

**Consequence: `syscall-coverage-map.md`'s central claim is STALE and is currently the main
argument-on-file against this directive. It should be corrected regardless of what happens to the
default**, because anyone reading it today would wrongly conclude the directive is unachievable.

## 3. True blast radius: 1 test, not 20

`docs/FAIL_CLOSED_STATUS.md` reports **20 known failures** with blockers `ioctl`(7), `tgkill`(4),
`mkdir`(3), `setitimer`(2), `clock_settime`(1), `getrlimit`(1), `kill`(1), `setsockopt`(1).

**None of those blockers still exists.** The machine-readable list —
`hermit-cli/tests/fail_closed_known_failures.tsv` — has **5 non-comment rows**:

| target | test | class | blocker |
|---|---|---|---|
| `fp_reduction_determinism` | `strict_parallel_fp_reduction_is_bit_identical` | host-pmu-bug | CI-runner AMD errata `AmdSpecLockMapShouldBeDisabled` → SIGSEGV |
| `hashseed_determinism` | `python_set_order_…_under_hermit` | host-pmu-bug | CI-runner SIGSEGV in reverie `clone_with_stack`; not reproducible locally |
| `hermit_modes` | `default_cargo_futex_and_print` | runner-scheduler-hang | privileged runner blocked >18 min; exact local test passes in 5.74 s |
| `signal_determinism` | `blocking_sigsuspend_releases_the_scheduler` | scheduler-hang | `rt_sigsuspend` does not wake under fail-closed |
| `sqlite_veryquick` | `sqlite_fast_subset_is_deterministic_under_strict_hermit` | **unsupported-syscall** | **Fchown** |

**Exactly one row (`Fchown`) is an unsupported-syscall blocker.** Two are CI-runner hardware
faults, two are scheduler hangs — none is caused by flipping the default. So the real cost of the
flip is **one test**, and `Fchown` is already a tracked determinization gap (hermit #1549).

`fail_closed_allowed_ignores.tsv` has **263** rows vs the doc's claimed 11, but they are
environment gates, not fail-closed debt: 213 are "ptrace-heavy rr program; requires PMU branch
counters and working mount namespaces," the rest PMU/toolchain/namespace prerequisites.

**Both docs are stale.** `FAIL_CLOSED_STATUS.md` still states "The normal command-line default
remains unchanged" and carries a 2026-07-22 blocker profile that the TSVs contradict. Its counts
(69/89, 20 known, 11 ignored) no longer reconcile with the files it points at.

> Note on an apparent contradiction in the task notes: the 2026-08-04 claim of "0/277 valid
> unsupported-syscall exceptions" and the doc's "20 known failures" are **different denominators**
> — the former is the e2e manifest corpus (657 total / 277 applicable), the latter the
> `hermit-cli` integration inventory. They were never in conflict; both are superseded by the TSV
> counts above.

## 4. A complete implementation already exists — do not rewrite it

Branch `codex/default-panic-unsupported-final` @ **`6d12a1edde6e4b1a662a2bdc15a014a097dfbf64`**
(local ref present; also `origin/…`). Diverged — base `c369be3f`, **not** an ancestor of main.
10 files, +209/−121:

```
detcore-model/src/config.rs                       |  2 +-
detcore/src/lib.rs                                | 19 +++--
detcore/src/syscall_classification.rs             | 12 +--
docs/ERROR_CATALOG.md                             | 10 +--
docs/FAIL_CLOSED_STATUS.md                        | 75 ++++-------
hermit-cli/src/bin/hermit/run.rs                  | 99 +++++++++++++-----
hermit-cli/tests/cli.rs                           | 54 +++++++++-
hermit-cli/tests/fail_closed_known_failures.tsv   |  1 -
hermit-cli/tests/kernel_keyring.rs                | 54 +++++++----
hermit-cli/tests/sqlite_veryquick.rs              |  4 +-
```

It does the right things:

- **Flips the default:** `config.panic_on_unsupported_syscalls = !self.allow_unsupported_syscalls;`
- **Adds the opt-out** `--allow-unsupported-syscalls`, with
  `conflicts_with_all = ["strict", "panic_on_unsupported_syscalls"]`, and emits a warning that it
  "permits unmodeled syscalls to reach the …" host
- **Brackets both directions in tests:**
  `unsupported_syscalls_fail_closed_by_default_with_explicit_opt_out` (bare run ⇒ *true*; opt-out ⇒
  *false*) and `passthru_optimization_requires_explicit_compatibility_opt_out`
- **Removes exactly one known-failure row** — matching the blast-radius finding above independently

## 5. What remains

1. **Rebase `6d12a1ed` onto `b64d893a`** and re-check `detcore/src/lib.rs`, which moved
   underneath it (the `Subscription::all()` change is in that file — verify the WIP's 19-line
   `lib.rs` hunk does not conflict with or revert it).
2. **Delete the contradicting assertion.** `run.rs:1085` asserts the fail-open default and *will*
   fail; the WIP replaces that test.
3. **Validate**, including the one genuine casualty (`sqlite_veryquick` / `Fchown`) — either fix
   `Fchown` or keep the row and don't remove it.
4. **Refresh both stale docs** — `FAIL_CLOSED_STATUS.md` (default claim, counts, blocker profile)
   and `syscall-coverage-map.md` (the 78-subscription / "cannot be trusted" argument).
5. **Open the PR.** Not possible this session: GitHub egress refused throughout
   (`api.github.com not allowlisted for agent_id agent:claude_code`).

**Not done here, deliberately:** no competing implementation was written and
`codex/default-panic-unsupported-final` was not touched — it is another agent's branch
(Invariant 5), and with no egress it could not be pushed anyway.

## 6. Review classification

Flipping this default changes the user-facing compatibility boundary: programs that previously ran
with a silent passthrough will now abort. It is **not** obviously one of the four
`post-facto-human-review` triggers (no new syscall support, no Reverie API change, no new
determinization strategy, no core scheduling change) — but it is a deliberate strictness/
compatibility trade the owner should confirm rather than have inferred. Worth an explicit owner
sign-off on the PR even though the label may not strictly apply.
