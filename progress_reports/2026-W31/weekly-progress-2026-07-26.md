# Weekly Progress - Monday, July 20 through Sunday, July 26, 2026

**Headline:** Hermit returned from dormancy with working CI, a much broader strict and record/replay test base, and five named alternatives to the ptrace golden reference moving from prototypes toward shared Detcore/Reverie execution.

## What shipped
- **Restored trustworthy automated testing.** The maintained `rrnewton/hermit` and `rrnewton/reverie` forks now separate portable hosted checks from CPUID/PMU hardware checks, enforce formatting and linting, and run strict, chaos, signal, pthread, record/replay, and real-application suites.
- **Expanded real-program coverage.** Python, Ruby, OCaml, Java, Node.js, Redis, SQLite, curl, nginx, compilers, shells, archives, networking tools, and developer utilities now exercise strict verification or record/replay. The record/replay compatibility set reached 128 named programs by Friday.
- **Established the backend portfolio by exact name.** ptrace remains the golden reference. KVM and DBI/DynamoRIO gained CPUID-capable execution, tools, files, processes, and lifecycle handling. SaBRe and LiteInst began running Detcore/Reverie tools in guest processes. e9patch gained cached ahead-of-time rewriting and L2 application coverage.
- **Fixed scheduler-visible syscall behavior.** `ppoll`, pipes, `epoll`, descriptor replay, signals, and unsupported-syscall refusal were brought under explicit deterministic policies.

## What it means
The project ended the week with a repeatable way to ask two separate questions: whether a real program is deterministic under ptrace, and whether KVM, DBI/DynamoRIO, SaBRe, LiteInst, or e9patch can reproduce that behavior. That distinction is the basis for honest backend parity.

## What's stuck
The backends still used different patching, tool-hosting, and global-state designs. ptrace could not intercept CPUID on the AMD host, so KVM/DBI remained essential. PMU crashes, terminal hangs, and incomplete process-tree behavior prevented a blanket green claim.
