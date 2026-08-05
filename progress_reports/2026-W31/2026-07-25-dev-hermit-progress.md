# Progress - Saturday, July 25, 2026

**Headline:** Hermit made strict compatibility a blocking gate and established usable e9patch, LiteInst, KVM, DBI/DynamoRIO, and SaBRe paths around the ptrace reference.

## What shipped
- **Made the strict compatibility envelope load-bearing.** New required workloads cover compilers, archives, networking tools, shells, math programs, data tools, system utilities, and developer tools. Unsupported syscalls now follow explicit deterministic-refusal policies instead of accidental host behavior.
- **Added e9patch and LiteInst as named execution paths.** e9patch gained cached ahead-of-time rewriting, L2 coverage for rewritten system tools and PHP, plus record/replay integration. LiteInst gained a Hermit compatibility backend and shared Reverie preload/runtime components.
- **Deepened KVM and DBI/DynamoRIO semantics.** KVM gained script-interpreter resolution, file descriptor operations, xattrs, priorities, directory changes, process waiting, and syscall-counting tools. DBI/DynamoRIO gained cross-process `GlobalState` over typed Unix-domain-socket RPC and deterministic time/RNG handling.

## What it means
Hermit can now compare the ptrace golden reference against five explicitly named alternatives: KVM, DBI/DynamoRIO, SaBRe, LiteInst, and e9patch. The blocking strict gate prevents compatibility claims from depending on optional local probes.

## What's stuck
These backends did not yet share one architecture. e9patch still combined ahead-of-time rewriting with ptrace runtime control, and the in-guest tool/state boundary for SaBRe and LiteInst still needed consolidation.
