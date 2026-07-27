# Injection strategies and current evidence

## What is already demonstrated

The preserved first iteration contains a real `LD_PRELOAD` `cdylib`. It
interposes `getuid`, `geteuid`, `getgid`, and `getegid`; lazily maps the RX code
and RW shared state; invokes the selected counter method; and restores `errno`.
The host launches a fork/exec tree and verifies the aggregate table. The guest
uses a test-only `dlsym` barrier, so the counter interception itself is unaware
but the fixture is not yet a completely stock binary.

The current executable-image runtime demonstrates a stronger image boundary:
authenticated code is copied to a sealed memfd, state uses a separate no-exec
memfd, processes map at independent addresses, and the loader validates method
offsets and ABI signatures before calling them. It now also has an unpublished
preload integration. The host supplies inherited non-`CLOEXEC` code/state FDs;
the shim authenticates and attaches lazily; and a guest with no pod dependency,
`dlsym`, or barrier recursively execs a process tree. A seven-process run
recorded exactly seven attachments and 1,407 controlled `getuid` calls,
including one preflight call per process before it could spawn descendants.

## Feasibility verdict

All three bootstrap mechanisms are feasible:

| Mechanism | Entry time | Guest changes | Steady-state supervisor | Main exclusions |
| --- | --- | --- | --- | --- |
| `LD_PRELOAD` | `exec` | none for dynamic guests | none | static/secure binaries, direct syscalls |
| ptrace plus detour | before or after `exec` | none | none after detach | ptrace/LSM policy, live-patch complexity |
| offline binary patch | before launch | rewritten artifact | none | per-ISA/format rewriting and hardening |

The pod interface should not depend on how it was injected. Each adapter should
produce the same validated process-local context and call the same explicit C
ABI. The library documentation describes that contract in
[`../v2/docs/injection.md`](../v2/docs/injection.md).

## Next proofs

1. Replace environment-provided trust and lazy allocating initialization with a
   versioned, authenticated, allocation-free bootstrap and descriptor channel.
2. Exercise a stock system binary and define constructor, loader-lock,
   allocator-reentrancy, signal, `fork`, exec, failure, and unload behavior.
3. Build a ptrace proof that stops all threads, maps both memfds, installs one
   reversible PLT/GOT or prologue hook, detaches, and observes calls afterward.
4. Add fault tests for hash mismatch, incompatible layout, address collision,
   partial attach, target exit, concurrent thread creation, and attempted
   unload with active calls.
5. Add one non-x86-64 target before treating detour code as a general facility.
