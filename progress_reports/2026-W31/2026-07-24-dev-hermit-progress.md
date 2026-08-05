# Progress - Friday, July 24, 2026

**Headline:** Strict verification and record/replay moved from small examples to broad command suites, while SaBRe, LiteInst, KVM, and DBI/DynamoRIO all gained concrete execution and lifecycle support.

## What shipped
- **Expanded the strict and record/replay envelopes.** The blocking strict gate gained compilers, Java, Node.js, Python, shell utilities, and developer tools. The record/replay compatibility set reached 128 named programs after fixes for descriptor namespaces, late ELF interpreters, `dup2`, `SIGPIPE`, and pipeline replay.
- **Brought SaBRe and LiteInst into the shared backend work.** Reverie SaBRe gained runner, `exec`, fork-safe RPC, and quiet compatibility execution; Hermit gained SaBRe integration and validation. Reverie LiteInst gained its preload instrumentation prototype.
- **Closed major KVM and DBI/DynamoRIO lifecycle gaps.** KVM gained fork/exec, files, memory syscalls, pipes, and guest exception handling. DBI/DynamoRIO gained process-group isolation, descendant cleanup, captured-output lifecycle, scheduler restart after failed `exec`, and file I/O through the guest interface.

## What it means
The project now has six named execution mechanisms under active comparison: ptrace as the golden reference, KVM, DBI/DynamoRIO, SaBRe, LiteInst, and e9patch. This day's changes made the first five capable of running meaningful program families instead of only unit demonstrations.

## What's stuck
The compatibility breadth was growing faster than exact cross-backend equivalence. SaBRe and LiteInst still needed shared-tool parity, and CI remained sensitive to runner setup and long-running compatibility jobs.
