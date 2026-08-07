# Per-member falsifiability control: reverie staging members

**Task:** `mutation-control-owed-before-landing-staging-members` · **Agent:** hermit-cc (opus-5) · **2026-08-07**
**Ran at:** reverie main `6144323c5dab8b521278fce206f8774360c2b05f`, worktree `worktrees/cc/reverie`
(all six member merge SHAs verified as ancestors of that head).

A passing test with no failing counterpart proves nothing. For each member this plants a defect in the
mechanism the test names, confirms the test **fails**, reverts, and confirms the clean control **passes**.

---

## 1. The control could not be run as specified, and that is the first finding

The task asks for the control **before landing**, excluding any member that cannot be made to fail. There
is nothing left to exclude:

- `staging/reverie-drain-all` (still at origin, head `f1aa47e1a268e51127dc5209acba05c97c8d32ea`, cut from
  reverie main `114b3df`) carried **9** members, not 18. The "18" was the count of open reverie PRs.
  The other nine never entered it: 312/330/335 conflicted (patching), 313/341 conflicted (other),
  221 excluded (withheld signature, `elf.rs:239`), 222 excluded (transitive on 221's branch).
- Of those 9, queried live: **6 merged**, **3 closed unmerged** (#338, #343, #352), **0 still landable**.
- Reverie main has since moved `114b3df` → `6144323c`, so the staging branch is stale against its own base.
- The hermit side is stale too, and says so: `staging-hermit-33-free-merges` carries the tag
  `stale-premise` (its premise was ~72 open hermit PRs; hermit is at 30).

The control was owed before landing; the batch landed first. Reporting "done, 0 members" would be a fake
green of exactly the kind this task exists to prevent — so the control was run **retroactively on the six
merged members**, where an unfalsifiable test is not a hypothetical about a batch but a live condition on
`main`.

## 2. Result — 6 of 6 falsifiable

| PR | merge SHA | test (fixture) | planted defect | clean | planted |
|---|---|---|---|---|---|
| #346 | `a47ae50434a4` | `reverie-kvm/src/executor.rs :: ppoll_masked_ready_returns_count_and_blocking_fails_closed` | `executor.rs:4011` `if args[4] != KERNEL_SIGSET_SIZE as u64` → `if false` | PASS | **FAILED** 0p/1f |
| #347 | `4e1ff64463a6` | `reverie-kvm/src/executor.rs :: unix_seqpacket_socket_autobinds_like_stream_and_dgram` | `executor.rs:4612` drop `\| libc::SOCK_SEQPACKET` from the accepted AF_UNIX types | PASS | **FAILED** 0p/1f |
| #350 | `774fdb68f9e5` | `reverie-kvm/src/executor.rs :: getsockopt_tcp_info_is_canonicalized` | `executor.rs:4703` `if args[1]==IPPROTO_TCP` → `if false && …` | PASS | **FAILED** 0p/1f |
| #361 | `cb092c6307ba` | `reverie-ptrace/src/timer.rs :: zero_skid_margin_forces_interrupt_at_target` | `timer.rs:231` `skid_margin()` ignores the override; **and** `timer.rs:190` override off-by-one | PASS | **FAILED** (both) 0p/1f |
| #361 | `cb092c6307ba` | `reverie/src/timer.rs :: record_then_take_counts_and_resets` | `take_skid_overshoot_count()` early-returns `0` | PASS | **FAILED** 0p/1f |
| #363 | `591154213a98` | `reverie-ptrace/src/perf.rs :: rdpmc_read_agrees_with_syscall_read` | `perf.rs:609` `unsafe fn rdpmc()` early-returns `0` | PASS | **FAILED** 0p/1f |
| #359 | `73695ea3ae09` | `reverie-dbi/tests/build_script_source.rs :: vendored_dynamorio_source_contains_no_binary_files` (+8 siblings) | wrote a 53-byte binary at `reverie-dbi/vendor/dynamorio/planted-mutation-control.bin` | PASS 9p | **FAILED** 8p/1f |

**0 members excluded on falsifiability grounds.** That is the opposite of the prior review's 12-of-18
failure rate. It is not read here as vindication of the batch — see §5.

## 3. One test that is not evidence

`#363`'s second test, `perf::test::bench_rdpmc_vs_read`, is `#[ignore]`d. It did not run in the clean arm
("1 ignored") and therefore **cannot fail**, so it contributes nothing to that member's evidence. `#363`
stands on `rdpmc_read_agrees_with_syscall_read` alone. A benchmark behind `#[ignore]` is a reasonable
thing to have; counting it as coverage is not.

## 4. A surviving mutant means "wrong mutant" until proven otherwise

The first mutation aimed at `#361`'s `zero_skid_margin_forces_interrupt_at_target` perturbed the **default**
`skid_margin` (1000 → 1001). **The test still passed.** That is not evidence of an unfalsifiable test — the
test name says `zero_skid_margin`, i.e. it exercises the **override** path, which the default never reaches.
The mutation missed the mechanism. Two correctly-aimed mutations (`skid_margin()` ignoring the override, and
an off-by-one in `with_skid_margin_override`) both failed it.

Recorded because the method matters: taking that first survivor at face value would have wrongly excluded a
good member. A surviving mutant is a claim about your mutant until you have shown it lands on the mechanism
under test.

## 5. What this does and does not establish

**Does:** each member's test can distinguish the mechanism it names from a broken version of that mechanism.

**Does not:** that the tests cover the members' full behaviour; that the mechanisms are *correct*; anything
about the 3 closed-unmerged members (nothing landed) or the 9 PRs that never entered staging. These are
unit-level tests — all seven run in ~0.04 s combined, and none exercises a real KVM guest or a real PMU
counter loop. Falsifiable at unit level was the bar this task set; it is not end-to-end coverage.

## 6. Reproduction

```bash
cd ~/work/dev-hermit/worktrees/cc/reverie && git checkout --detach origin/main
# clean arm
cargo test -p reverie-kvm --lib -- ppoll_masked_ready_returns_count_and_blocking_fails_closed \
    unix_seqpacket_socket_autobinds_like_stream_and_dgram getsockopt_tcp_info_is_canonicalized
cargo test -p reverie-core  --lib -- record_then_take_counts_and_resets
cargo test -p reverie-ptrace --lib -- zero_skid_margin_forces_interrupt_at_target \
    rdpmc_read_agrees_with_syscall_read
cargo test -p reverie-dbi --test build_script_source
# planted arm: apply one edit from the table, re-run that test, then `git checkout -- <file>`
```

Every mutation above is reverted; the worktree and the reverie primary were left clean.
