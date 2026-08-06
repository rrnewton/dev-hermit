# LiteInst corpus broadening: the target set, and the lane is red on main

- **Task:** `liteinst-corpus-broaden-toward-100` (north star #89; provenance #268).
- **Author:** impl agent, claude-opus-5. Local only, no egress, no concurrent validate.
- **Tree:** slot `worktrees/oci/hermit` @ `5562161a4`, base hermit main `b64d893ae9ea`.
- **Companions:** `liteinst-strict-parity-ratchet-blocked-20260806.md` (the activation regression),
  `liteinst-detlog-heap-stack-domain-20260806.md` (the domain question).

---

## 1. The blocker is now MEASURED by the repo's own gate, not inferred

The two prior tasks reported that `--backend liteinst` fails to activate, based on my own CLI
invocations. That left open the possibility I was invoking it wrong. **Settled:** I ran the
authoritative harness from `validate.sh:4767-4770`:

```
HERMIT_LITEINST_TEST_BINARY=<slot>/target/release/hermit \
cargo test --release -p hermit --features third-party-backends --test liteinst_advanced -- --test-threads=1

running 23 tests
test result: FAILED. 1 passed; 22 failed; 0 ignored; finished in 1.50s
```

**22 of 23 fail**, with the identical error:

```
stderr=Error: verify LiteInst runtime activation failed for tracee N:
       tracee terminated before the required preload handshake completed (phase Waiting)
```

The single pass is `liteinst_preload_is_inert_without_host_runtime_selector` — a test that asserts
the preload does **nothing**. So **every test requiring LiteInst to actually activate fails**, and the
one green test is green precisely because it exercises the inert path.

This is third-party checkable and independent of how I drive the CLI. **The LiteInst compat lane
cannot be green on `b64d893a`.**

### Where the handshake dies

`reverie-ptrace/src/task.rs:720-725` defines `PreExec → Waiting → Bootstrap → Ready`. Advance happens
on guest traps classified as `HandshakeBegin` (Waiting→Bootstrap) and `HandshakeReady`
(Bootstrap→Ready, `task.rs:2360`). Runs stall at **`Waiting`**, so **`HandshakeBegin` never arrives** —
the preload constructor never signals the host. Consistent with the guest emitting zero stdout, while
the same DSO loads fine under the ordinary loader (`LD_PRELOAD=<so> /bin/echo` → rc 0, constructor
symbol and `INIT_ARRAY` present). Wiring on the hermit side looks intact
(`hermit-cli/src/lib.rs:1552-1557` → `LiteinstBackend::run_host_with_preload`), so the fault is
between the loader running the constructor and the host seeing its trap.

## 2. The target set this task asked for — with the denominator

From `compat-envelope/scorecard.csv` (the only cross-backend corpus record that exists):

| Quantity | Count |
|---|---:|
| `test_id`s passing under **ptrace** | **72** |
| `test_id`s passing under **liteinst** | 136* |
| **ptrace-passing but NOT liteinst-passing — the target set** | **43 of 72 (60%)** |
| …of which have **no liteinst row at all** | **24** |
| …of which have a liteinst row recording `fail` | 19 |

\* the liteinst pass count is larger than the ptrace one because the two backends were scored over
different runs and different `test_id` populations; **the 72 is the denominator that matters** for
"ptrace-passing programs not yet green under liteinst".

The 24 with **no row at all** are the whole `backend-parity/*` family — `hello_stdout`, `exit_zero`,
`heap_growth`, `file_read`, `virtual_clock`, `random_sources`, `pthread_lifecycle`, `cpuid_policy`,
`anonymous_mmap_layout`, `executable_mmap`, `shared_anonymous_mmap`, `memory_advice`,
`process_vm_readv_refusal`, `process_vm_writev_refusal`, `process_wait_accounting`,
`process_wait_lifecycle`, `scheduler_policy_queries`, `io_uring_fallback`, `listmount_unavailable`,
`file_metadata`, `file_mutation`, `exit_status`, `argument_forwarding`, and the `backend-parity`
`heap_growth` variant.

**That is the finding for prioritisation: the largest bucket is not "liteinst fails these", it is
"liteinst was never run on these."** 24 of the 43 gaps are *unmeasured*, not *failing*. A "parity %"
computed over only the rows that exist would silently exclude them — the same denominator error that
makes a stdout-only metric look good.

Also carried forward: **all 220 liteinst rows are `cell_state: disabled`**, and every row's `reason`
records the basis as `parity=exit+stdout vs ptrace`. So even the 136 "passes" are stdout+exit, not
detlog. Nothing in this corpus measures the `#89` standard.

## 3. Why no program was fixed

The task asks to "fix ptrace-passing programs not yet green under liteinst, by scorecard priority."
With the backend unable to activate, **every one of the 43 would fail identically and for the same
non-program reason**. Fixing them individually is not possible and would not be meaningful; the
scorecard-priority ordering only becomes actionable after activation is restored. I did not run the
corpus and I am reporting no parity numbers.

## 4. Gaps

| # | Gap | Status |
|---|---|---|
| C1 | **LiteInst activation regression** — repo's own `liteinst_advanced` harness 22/23 red on main; stalls at phase `Waiting`; regression vs ancestor `464cbd9f` (still **UNVERIFIED** at that SHA — reproduce green there first) | blocks everything below |
| C2 | **24 of 43 target programs have never been run under liteinst** (`backend-parity/*` family). Unmeasured ≠ failing; any parity % must carry them in the denominator | measurement gap |
| C3 | The corpus's parity basis is **stdout+exit**, so the corpus cannot express `#89` detlog parity at all | oracle gap |
| C4 | All 220 liteinst rows are `cell_state: disabled` — these cells gate nothing today | wiring gap |

**Order:** C1 → C2 (extend the corpus to the 24 unmeasured, which is cheap once the backend runs) →
C3 (the detlog comparator, the real `#89` deliverable) → C4.

## 5. Limitations

- **No corpus run, no parity numbers.** Everything quantitative above is read from
  `compat-envelope/scorecard.csv` plus one harness invocation.
- The scorecard's ptrace and liteinst rows come from **different runs at different SHAs**
  (`464cbd9f9bb4` for liteinst; ptrace rows from a separate canonical run), so the 43 is a
  set-difference across non-simultaneous measurements, not a matched pairing. It is the right
  work-list; it is not a parity statistic.
- I did **not** bisect the 22-commit regression window, and did not verify green at `464cbd9f`.
- One harness run, one host, release profile only.
