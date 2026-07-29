# Connector adversarial review, round 6

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed published implementation:
`b2efc0b6118ea83b66ed3249283d99e706077436`.

## Verdict

**REJECT.** Two at-fork fail-stop claims are false under adversarial but valid
process state.

## Blockers

1. The fail-stop path writes a diagnostic to stderr before `_exit(125)`.
   `raw_write` performs a blocking `write(2)`. A C harness using the exact
   published shim terminated from `SIGPIPE` (shell status 141) when stderr was
   a broken pipe, and hung until `SIGALRM` (status 142 after two seconds) when
   stderr was a full blocking pipe. The shipped regression uses a healthy,
   empty pipe and therefore cannot establish bounded exit status 125.

2. `ForkBarrier::prepare` caches PID/TID before publishing its owner CAS. LLDB
   stopped the exact shim after that cache and delivered `SIGUSR1` to a C
   handler that called libc `fork`. In the nested child, the interrupted outer
   prepare resumed with the parent's stale identity, published it, and
   performed another fork. Its parent branch eventually detected corruption,
   but the child callback accepted and rebound the stale identity, so an extra
   child survived. The observed output included both:

   ```text
   outer-child resumed-after-signal-fork
   outer-child ordinary
   ```

   Revalidate the current PID/TID immediately after acquiring the owner CAS
   and fail stop without I/O on mismatch. Add a deterministic regression at
   this exact pre-claim signal/fork cut.

## Passing evidence

- Shim unit tests: 12 passed, 0 failed.
- The DSO remained callable and resident after `dlclose`.
- Preload demo: 3 processes, 243 exact calls, 3 attachments.
- Ptrace bootstrap counted the call and attachment, then resumed the target
  after detach.
- Public status values and C/Rust connector layouts were consistent by
  inspection.

All tests used an isolated `/tmp` archive of the published commit. The reviewer
modified no repository files.
