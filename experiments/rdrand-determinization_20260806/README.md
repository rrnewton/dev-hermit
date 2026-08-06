# RDRAND/RDSEED determinization: probes and PR bodies

Working directory for the randomness-determinization lane (2026-08-06). The `.c`
files are the planted probes; the `PR-BODY-*.md` files are the descriptions of the
PRs they justify, kept here so the evidence and the claim stay together.

Compiled binaries are deliberately NOT committed; rebuild with plain `gcc -O1`.

| probe | what it plants |
| --- | --- |
| `rdrand_forced.c` | RDRAND/RDSEED issued WITHOUT consulting CPUID — the hole CPUID masking hides |
| `premise_ud2.c` | a bare `ud2`, to confirm a guest SIGILL reaches Detcore's signal handler |
| `control_det.c` | positive control: an ordinary deterministic program, must not be flagged |
| `dso_main.c` + `librand_plant.c` | RDRAND in a shared library, to cover the DSO path |

PRs these support: #1671 (RDRAND determinization + DBI fence), #1686 (getrusage CPU
time), #1689 (DBI `--log-file`), #1695 (DBI heap emission), #1710 (randomness and
lock fixtures), #1742 (flock mutual exclusion), #1747 (SaBRe handshake guard).
