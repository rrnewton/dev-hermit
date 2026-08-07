# Why no bitwise comparator was ever wired

**Answer: it EXISTS AND IS NOT SELECTED.** Not "never built", not "built and
removed". The capability is present, functional, and inert — which makes this a
selection fix, not an implementation.

Research only; nothing was changed.

## Freshness first

The dispatch warned that several checkouts are hundreds of commits behind, and
they are. Measured before searching anything:

| checkout | behind `origin/main` |
|---|---:|
| parent primary | **297** |
| hermit primary | 21 |
| `worktrees/w17/hermit` (mine) | 1 |

Every claim below is from `origin/main` **refs** (`git show origin/main:<path>`,
`git grep … origin/main`), never from a working tree.

## The three-way verdict, with the evidence for each branch

### Never built? NO.

`BitwiseInfoV1` is fully implemented at hermit `origin/main` in
`hermit-cli/src/bin/hermit/verify.rs`, with an explicit policy contract (strip
only the wall-clock prefix, ordinalize host addresses preserving identity/order/
aliasing, compare the full remainder exactly). The CLI flag is real:
`run.rs:336 verify_strict: bool`, consumed at `run.rs:608`.

### Built and removed? NO.

Pickaxe over **all** commits, both repositories:

- `git log --all -S '--verify-strict' -- ci/test_harness.sh` → **empty**. The
  string has never appeared in the harness, so it cannot have been removed from it.
- `git log --all -S 'verify-strict' -- compat-envelope/` → two hits, `e9073a5`
  and `7eb631d`, and **both are prose, not argv**. `7eb631d` states the situation
  outright in its own metadata: *"the collector builds every Hermit argv in one
  place … and **never passes --verify-strict**, so the --verify leg selects the
  default Stripped comparator"*.

### Exists but not selected? YES.

The selection point is `hermit/ci/test_harness.sh`, which the compat-envelope
collector drives rather than reimplementing. At `origin/main`:

| flag | occurrences in `ci/test_harness.sh` |
|---|---:|
| `--verify` | **4** (lines 1565, 1569, 1629, 1634) |
| `--verify-strict` | **0** |
| `--verify-json` | **0** |

All four invocation sites pass plain `--verify`, which per hermit's own AGENTS.md
"uses the lossy `Stripped` comparator and cannot establish L2".

## One cell, end to end — the unknown converted to a known

Rather than scope a population-wide change, I ran a single cell both ways. Same
guest, same backend, same binary; **only the compare-mode flag differs.**

```
hermit --log=info run --backend ptrace --strict --verify [--verify-strict] \
       --verify-json <out> -- <guest>
```

| leg | verified | `bitwise_parity` | verdict | compared log messages |
|---|---|---|---|---|
| plain `--verify` — what the harness selects today | true | **false** | matched | 84 / 84 |
| `--verify-strict` — never selected anywhere | true | **true** | matched | **107 / 107** |

**The bitwise comparator works.** It returns `bitwise_parity: true` on a real
cell, and it compares **23 more messages than the stripped policy** — 107 vs 84,
so Stripped discards 21% of the stream before comparing.

This is what makes the classification actionable: the column is blank not because
the measurement is hard, but because nobody asks for it.

### A no-result I nearly recorded as a measurement

My first attempt ran both legs at `--log=off` and both returned
`bitwise_parity: false`. That looked like a clean answer. It was not — the JSON's
own `verdict` field read `no_result`, and stderr said
`--verify requires --log=info or a more verbose level; received --log=off`. The
guest ran, the comparison never did. I had briefly written down "plain --verify
reports bitwise_parity:false" as a finding before reading the verdict field.

The lesson is the one this codebase keeps re-learning: **`bitwise_parity: false`
and `verdict: no_result` are different facts, and a schema that carries both is
the only reason the error was catchable.** Always read the verdict before the
payload.

### An observed cost, not an explanation

The strict leg is far slower: plain `--verify` completed in seconds, while
`--verify-strict` exceeded a 900s bound on its first run and completed only under
a 2700s bound. That is a real cost and a plausible *motive* for defaulting to
Stripped — but it is **not** evidence that anyone chose it for that reason. The
history shows no such decision, because the flag was never wired at all.

## What this implies for the work

The downstream blank `bitwise_parity` in the scorecards is a **correct**
consequence, exactly as `collect-envelope.rs:431-434` documents: *"Blank, not
'0': this harness does not read --verify-json … a 0 would assert a measurement
that was never taken."* That code is right and should not be changed first.

The change is upstream, in two parts — so "config fix" is close but slightly
understated:

1. **Selection** — pass `--verify-strict` at the harness's four invocation sites.
   That is a flag.
2. **Observation** — read `--verify-json`. Neither the harness nor the collector
   reads it today, so even with the flag set, the verdict would not reach a row.
   That is a small amount of parsing, not a flag.

Both are far short of implementing a comparator, which already exists and works.

## Limitations

One cell, one guest, one backend (ptrace), one host. The 84-vs-107 message counts
are specific to this guest. The slowness figure is a bound, not a measurement: I
recorded that strict exceeded 900s and finished under 2700s, not how long it took.
