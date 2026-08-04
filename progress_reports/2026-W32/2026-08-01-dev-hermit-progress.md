# Progress — Saturday, August 1, 2026

**Headline:** A large, steady push to make Hermit's different ways of running a program all behave identically to its trusted reference method — roughly a dozen correctness fixes across three run engines — plus a change that made one engine's warm-up about 28× faster.

## What shipped
- **Brought three alternative run engines into exact agreement with the reference.** Hermit can run a program through several different low-level engines. The trusted, slowest one is the yardstick; the faster ones must produce identical behavior to be usable. About a dozen fixes today closed gaps where a faster engine disagreed — matching how the reference reports network socket timestamps and identities, keeping standard input/output stream identity consistent, and aligning how the program's own process identity appears. One fix stopped a run engine from leaking the real machine's host name into the program, which both broke identical-run behavior and leaked a host detail a program shouldn't see.
- **Made one run engine's warm-up about 28× faster.** By remembering results it had already computed instead of recomputing them on every run, one engine's repeated-run preparation dropped dramatically. This is preparation cost, so it does not change what the program computes — it just gets there far quicker on the common repeated-run path.
- **Widened the safety net of automatic identical-run checks.** New checks were added for more real programs — cryptographic certificate handling, system-uptime queries, random-number sources — and several stress programs were promoted from optional to required, so a regression in them now blocks a change instead of being ignored.
- **Made it routine to build and check every run engine.** The build system now builds all the engines by default and offers a one-command check per engine, and a new front-door tool makes the library of real-program test cases easy to run. This lowers the chance that one engine quietly rots while attention is on another.
- **Wrote down the design for running standard container images deterministically.** A design document now describes how Hermit will run a standard packaged application image so it behaves identically every time — the groundwork for a prototype already in progress.

## What it means
Hermit's core promise is running a Linux program so it behaves identically on every run. The faster run engines are how that promise becomes practical on real workloads instead of only on the slow reference — so today's work is squarely on the critical path: every closed disagreement is one more real program that a fast engine can run without giving up the guarantee, and the wider automatic checks are how those agreements are kept from silently breaking later.

## What's stuck
Nothing was blocked or lost today. The one caveat is scope: this was many small, individually-modest fixes rather than one headline feature — real progress toward full agreement across engines, but the kind that is only visible when counted together, which is why it is counted here.
