# Progress - Friday, July 31, 2026

**Headline:** Hermit made virtual time continuous across process trees and added explicit parity harnesses for SaBRe, LiteInst, and e9patch, while the QEMU demo regression was narrowed to the ptrace notifier path.

## What shipped
- **Unified guest time across processes.** The guest clock now tracks committed logical time across a process tree, with continuous-time tests for LiteInst and SaBRe. This avoids per-process time resets and preserves nanosecond-scale virtual-time semantics.
- **Made backend coverage explicit.** SaBRe gained a named supported envelope and required C-corpus cells; LiteInst gained digest, formatting, and utility corpora; e9patch gained an ahead-of-time preprocessing parity harness.
- **Kept third-party backends optional at build time.** DBI/DynamoRIO, SaBRe, and e9patch are behind a Cargo feature so the default Hermit binary does not silently depend on external backend builds.
- **Extended KVM DETLOG evidence.** Reverie KVM now reports guest stack and heap regions, and synchronized `waitid` child processing.

## What it means
Virtual time is a shared process-tree property, and backend support is represented by named, runnable corpora rather than prose claims. ptrace remains the golden reference for full DETLOG comparison.

## What's stuck
The QEMU/BusyBox demo still took roughly 345-373 seconds instead of its prior 46-47 seconds. Investigation had isolated a ptrace notifier regression, but the fix and final proof had not yet landed at the end of this report day.
