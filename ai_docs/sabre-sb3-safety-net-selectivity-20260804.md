# SaBRe sb-3 safety-net selectivity audit

Date: 2026-08-04

Source audited: `rrnewton/hermit` at `d7938ce654b42b4a02b19b70fa9701a8e48a2f6c`

## Verdict

The current sb-3 mechanism **requires a ptrace syscall stop on every syscall entry and exit in
order to detect a missed SaBRe rewrite**. It cannot fire only on missed sites as implemented.

The decisive ordering is:

1. `PTRACE_SYSCALL` causes the generic syscall stop.
2. Only after that stop, the supervisor computes `site = RIP - 2` and reads the tracee bytes.
3. Only then can it decide whether those bytes are the raw `0f 05` instruction that SaBRe missed.

The readiness flag and trusted-mapping predicate narrow whether a stopped syscall site is patched;
they do not narrow which syscalls stop. No code path installs a site-specific trigger before the
miss is observed, and no code path switches a tracee from `PTRACE_SYSCALL` to `PTRACE_CONT` after a
warm-up.

Therefore the present safety net is architecturally a persistent ptracer, not a rare fallback. A
selective replacement would need a different mechanism that proves or patches all relevant sites
before they execute. Whether SaBRe may drop the safety net or must retain the persistent ptracer is
the reserved owner decision.

## Code evidence

All line numbers below are from `hermit-cli/src/sabre_ptrace.rs` unless noted.

- The module states its purpose directly: "Ptrace safety net for syscall instructions missed by
  SaBRe rewriting" (`:9`).
- Every SaBRe run enters this supervisor unconditionally through
  `sabre_ptrace::run(...)` (`hermit-cli/src/lib.rs:1052-1060`). The caller says it returns only after
  every tracee reaches a final kernel wait status (`hermit-cli/src/lib.rs:1070-1072`).
- The supervisor attaches to the root, installs ptrace options, and resumes it with
  `ptrace::syscall`, explicitly reported as `PTRACE_SYSCALL` (`:148-166`).
- `PTRACE_O_TRACESYSGOOD` is installed together with clone/fork/vfork/exec/exit tracing
  (`:337-347`). `WaitStatus::PtraceSyscall` is dispatched to `handle_syscall_stop` (`:250-252`).
- The missed-site decision happens **inside** that already-delivered syscall stop. On an entry it
  reads registers, computes `site = regs.rip - 2`, reads two instruction bytes, and only then tests
  `bytes == [0x0f, 0x05] && fallback_ready && !trusted_mapping` (`:351-380`).
- For a match it writes the SaBRe marker, suppresses the in-flight raw syscall with
  `orig_rax = -1`, and records a pending patch (`:379-386`). The following syscall-exit stop
  restores the syscall number and rewinds RIP to the newly installed marker (`:395-402`). Thus the
  first missed execution itself consumes both the generic entry and exit stops.
- Every syscall-stop handler resumes with `ptrace::syscall` again (`:406`). Every other signal and
  ptrace-event resume also calls the helper whose sole operation is `ptrace::syscall`
  (`:424-429`). There is no `PTRACE_CONT` path.
- Clone/fork/vfork children are added to the traced set, and exec clears caches/state but does not
  detach (`:410-424`). The supervisor loop continues until the traced set is empty (`:168-169`).
- `fallback_ready` is sampled only after the syscall stop and only gates the patch predicate
  (`:368-379`, `:431-440`). Likewise, trusted mapping classification is evaluated only after the
  stop (`:379`, `:443-452`). Neither can make stop delivery selective.

## Architectural boundary

The raw missed instruction is a valid x86-64 `syscall`, not a trap instruction. In this code, its
distinguishing fact is the instruction bytes at the stopped RIP, not the syscall number or
arguments. Until the generic entry stop supplies RIP, the supervisor has no miss signal to inspect.

This finding is intentionally limited to sb-3's current detection mechanism. It does not decide the
owner tradeoff between removing the net after an external exhaustiveness proof and accepting that
SaBRe remains a persistent-ptrace backend.
