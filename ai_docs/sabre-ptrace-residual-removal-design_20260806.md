# SaBRe's residual ptrace dependency: what it is, why the cheap removals don't work, and the one that does

**Task:** `ptracer-removal-2-sabre-residual` (step 2 of the owner-gated ptracer-removal sequence)
**Agent:** hermit-det4 (`[impl agent, opus-5]`) · **2026-08-06** · local, no egress
**Base:** hermit main `4c70658e785834737cbe1524f77330c781a6f5ea`
**Status: NOT REMOVED. Design + blocking analysis only — no product change was made.**
Read §5 before writing code against this; it explains why I stopped.

## 1. What the residual actually is

`hermit-cli/src/lib.rs:1056` calls `sabre_ptrace::run(...)` **unconditionally** on the
`--backend=sabre` path. There is no flag, no capability check, and no early-out: every SaBRe run
is a ptraced run.

`hermit-cli/src/sabre_ptrace.rs` (1843 lines, "Ptrace safety net for syscall instructions missed by
SaBRe rewriting") attaches to the SaBRe process and resumes every tracee with `ptrace::syscall(...)`
(`:301`, `:582`, `:610`). That means **a syscall-entry stop and a syscall-exit stop for every
syscall of every thread, for the entire run** — not just for the sites SaBRe missed.

What it does with those stops (`handle_syscall_stop`, `:502`):

* **entry** — `getregs`, compute `site = rip - 2`, `read_two_bytes(pid, site)` (a second ptrace
  round trip). If the bytes are a raw `0f 05` **and** the mapping is untrusted, overwrite them with
  SaBRe's `0f ff` marker, cancel the syscall (`orig_rax = u64::MAX`), and record a pending patch.
* **exit** — if the syscall mutated the address space, invalidate the mapping cache; if a patch is
  pending, rewind `rip` to the site so the guest re-executes the now-marked instruction, which
  SaBRe's in-guest SIGILL handler then serves.

So the supervisor's *purpose* is **one-time conversion of raw syscall sites into SaBRe-handled
sites**. The work is O(distinct raw sites). The mechanism it is implemented with is O(syscalls).
**That gap is the residual.**

## 2. Why it cannot just be switched off

The discriminator is "what are the two bytes at `rip - 2`". That is only observable at a stop. Drop
the stops and a raw, unconverted `0f 05` executes **natively, un-intercepted by Detcore** — a silent
loss of determinism, which is strictly worse than the current cost. The safety net is load-bearing.

## 3. Three cheaper removals, each checked and each refuted

| candidate | why it fails |
| --- | --- |
| **Keep entry stops, skip the exit stop** (the exit is a no-op for most syscalls) | Not expressible. `PTRACE_SYSCALL` arms stops in pairs: entry → `PTRACE_SYSCALL` → exit → `PTRACE_SYSCALL` → next entry. Issuing `PTRACE_CONT` at an entry stop cancels the exit stop *and every subsequent entry stop*, so you lose discovery entirely. There is no "resume to next entry, skipping this exit". |
| **Seccomp-filter the stops** (`PTRACE_O_TRACESECCOMP` + `SECCOMP_RET_TRACE`) so only interesting syscalls trap | A seccomp filter selects on syscall **number** and register values. A raw site can issue *any* syscall number, so no nr-based filter can identify one. Seccomp cannot see the instruction bytes at `rip`. |
| **Detach the supervisor once discovery quiesces** (no new sites for N syscalls) | Quiescence is not a proof: a later `mmap(PROT_EXEC)`, `mprotect(+X)`, `dlopen`, or JIT emission can introduce a fresh raw site at any time. Detaching needs a re-arm trigger on those events — which is the seccomp mechanism in the row above, and that one *does* work for this narrower question (see §4). |

## 4. The removal that does work

**Replace dynamic per-syscall discovery with mapping-time discovery.**

1. **Scan, don't trap.** When an executable mapping appears (at exec, and at any `mmap`/`mprotect`/
   `mremap` that grants `PROT_EXEC`), scan the untrusted executable pages for `0f 05` and convert
   every occurrence to `0f ff` up front. Trusted regions (SaBRe's own code, the plugin) are skipped
   exactly as `classify_mapping` already decides today.
2. **Wake only on mapping changes.** Install a seccomp filter with `SECCOMP_RET_TRACE` on the small,
   *number-identifiable* set `{execve, execveat, mmap, mprotect, mremap}` and resume everything else
   with `PTRACE_CONT`. This is the one thing seccomp *can* express here, because these are selected
   by nr, unlike raw-site discovery.
3. **Then the supervisor is off the syscall path.** It wakes O(mapping changes) times instead of
   O(syscalls), and ordinary syscalls run entirely through SaBRe's in-guest handler.

The existing machinery mostly survives: `classify_mapping`, the trusted/untrusted decision, the
address-space-keyed `mapping_cache` with its CLONE_VM sharing logic, and the `PathEvidence` counters
are all reusable. What changes is *when* they run.

**Known residual risk in this design, stated rather than buried:** a guest that writes instruction
bytes into an already-executable page (a JIT patching in place, without a fresh `mprotect`) creates a
raw site that no mapping-change trap will catch. Today's per-syscall scan catches it; the redesign
does not. That is a real narrowing of the safety net and needs an explicit decision — either accept
it and document it, or keep a `W^X`-style trap for writable-executable pages. **This is the design
question I am not authorised to settle unilaterally.**

## 5. Why I did not implement it

1. **The failure mode is silent.** Getting this wrong does not crash; it lets a syscall run
   un-intercepted, which shows up later as unexplained nondeterminism. A partially-verified version
   of this change is worse than the current cost.
2. **It changes the safety net's coverage** (§4, JIT case). That is an architecture decision inside
   an owner-gated sequence, not an implementation detail.
3. **I cannot validate it on this box.** See §6 — the only SaBRe-capable binary available predates
   the current source, so I have no valid behavioural baseline to compare a change against.

## 6. A measurement trap the next agent will hit

The only hermit binary on this host with SaBRe compiled in is
`worktrees/dbi/hermit/target/release/hermit` (`g52d56e5c`, built 2026-08-04). **It predates the
current `sabre_ptrace.rs`:** `strings` finds zero occurrences of `HERMIT_SABRE_PATH_EVIDENCE`, and
its completion line is the old shape `SaBRe ptrace fallback completed patched_sites=0` rather than
the current `ptrace_fallback_sites=… trusted_shared_object_sites=… guest_rpc_observed=…`.

**Any behavioural claim about the residual made with that binary is invalid.** In particular
`patched_sites=0` from it does *not* establish that the current code converts no sites. Build from
`4c70658e7` with `--features third-party-backends` first.

## 7. A correction to the framing this step inherits

The sequence treats SaBRe's ~157 µs/syscall (vs ptrace's ~34 µs, from
`verify-ptracer-out-of-path`) as motivation for removing the residual. Note what that arithmetic
implies: **plain ptrace also takes two stops per syscall and costs ~34 µs**, so the supervisor
accounts for roughly a ptrace-equivalent share and the remaining ~120 µs is SaBRe's own machinery —
the SIGILL round trip plus the coordinator RPC over the unix socket.

So removing this residual is an **architecture** fix, and it will **not** by itself bring SaBRe near
ptrace. Anyone expecting step 2 to close the perf gap will be disappointed for reasons that have
nothing to do with whether step 2 was done correctly. (Those µs figures are quoted from the existing
measurement task; per this task's instruction I did not measure hop costs myself.)

## 8. Also worth checking: is step 2 really independent of step 3?

The task states step 2 is "independent of the shared-host hoist, so it can proceed in parallel with
step 1". The design in §4 is indeed independent — it is about *discovery*, not about where the
ToolHost lives. But note that the alternative framing ("make SaBRe's in-guest handler cover the
missed sites so no supervisor is needed") *is* the shared in-guest handler work, i.e. step 3. If the
owner's intent for step 2 was the second framing rather than the first, then step 2 is **not**
independent and the sequence should say so. Flagging because the two readings lead to different
work.
