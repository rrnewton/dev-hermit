# Measurements index

One row per number we quote. `measurements-index.csv` is the index; this file is
the discipline.

**The `rederive_command` column is the load-bearing one.** An index of stale
numbers is a prettier version of the problem it was meant to solve. If a row has
no command that reproduces its value, the row is worthless — delete it or fix it.

## Why this exists

Four real failures in a single day, all the same shape — a number quoted without
the thing that makes it mean something:

| quoted | actual problem |
| --- | --- |
| `40/53` vs `9/32` branches | **different populations**, compared as if one |
| `~193` vs `106` commits | a **tag count** quoted as a commit count |
| DBI "B1" vs `~85%` | a **week-old** number quoted as current |
| `0/346` bitwise | measures **stdout only**, quoted as full parity |

None of these were lies. Each was a real measurement that lost its denominator,
its date, or its conditions somewhere between being taken and being repeated.

## The columns

- **`value`** — never a bare ratio. `50` means nothing; `50 of 100 identical
  getppid calls` means something.
- **`date`** — when it was *measured*, not when it was written down.
- **`denominator`** — what the number is out of, and explicitly **what it is
  not**. Several rows say "NOT a tag count", "NOT the LBR depth", "NOT total
  runtime". Those negations are there because someone already made that mistake.
- **`rederive_command`** — the command. Not a description of the command.
- **`conditions`** — host, build profile, fixture, and anything that makes the
  number not transfer.
- **`status`** — see below.

## Status values

| status | meaning |
| --- | --- |
| `CURRENT` | re-derived at the stated date and expected to hold |
| `VOLATILE-REDERIVE-ALWAYS` | changes continuously; never quote the stored value |
| `HOST-SPECIFIC` | true on this box; re-measure elsewhere before quoting |
| `RE-VERIFY-BEFORE-QUOTING` | older than the current work; may have decayed |

**Anything older than the current work must be marked
`RE-VERIFY-BEFORE-QUOTING`.** Staleness is invisible in prose — a number from
last week reads exactly like a number from this morning.

## A worked example of the decay, from building this index

`parent_unpushed_commits` was **121** when measured earlier the same day. By the
time this index was written it was **8** — the parent had been pushed twice in
between. Nothing about the number "121" announced that it had expired, and it
was already being cited in a report.

That is why that row is `VOLATILE-REDERIVE-ALWAYS` rather than carrying a value
anyone should reuse. Some numbers are not facts; they are readings.

## Adding a row

Take the measurement, then write the row from the terminal you took it in, while
the command is still in your history. A row reconstructed later from memory tends
to lose exactly the conditions that mattered.

If you cannot write the `rederive_command`, you do not yet understand the
measurement well enough to quote it.
