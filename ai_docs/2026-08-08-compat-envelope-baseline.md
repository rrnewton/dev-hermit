# Compat-envelope baseline, measured 2026-08-08

**Task:** `establish-current-compat-envelope-baseline`. Measure what the compat envelope
currently IS. Do not expand it.

**Bottom line, stated three ways because the three numbers are different and all three get
quoted as "the envelope":**

| Question | Answer | Denominator |
| --- | ---: | --- |
| Rows whose `comparison_tier` **claims** a qualifying tier | **6** | 2,290 rows across 4 scorecards at HEAD |
| Rows whose qualifying claim is **evidenced** by its own row | **0** | 6 claims |
| **Cross-backend** cells at a qualifying tier | **0** | 463 non-ptrace cells |

The number to plan against is **0 evidenced cells**. The `6` is a label count, and the
repository's own evidence checker refuses all six. Details in §4.

---

## 1. Source discipline — read this before re-deriving

`compat-envelope/scorecard.csv` **was dirty in the working tree while this was measured**
(736 data rows vs 624 at HEAD). That is the known appending-producer defect
(`scorecard-csv-appends-instead-of-replacing`, separate task, fix in flight). Every number
below is taken from the **committed** file, never the working copy:

```bash
mkdir -p /tmp/env
git show HEAD:compat-envelope/scorecard.csv            > /tmp/env/scorecard-HEAD.csv
git show HEAD:compat-envelope/reverie-scorecard.csv    > /tmp/env/reverie-scorecard.csv
git show HEAD:compat-envelope/e9patch-scorecard.csv    > /tmp/env/e9patch-scorecard.csv
git show HEAD:compat-envelope/fullcorpus-scorecard.csv > /tmp/env/fullcorpus-scorecard.csv
git show HEAD:compat-envelope/corpus-manifest.csv      > /tmp/env/manifest.csv
```

Measured at dev-hermit HEAD with `compat-envelope/scorecard.csv` at **624 data rows,
0 blank `comparison_tier`** — the state `scorecard-fixer` restored. Confirm with:

```bash
python3 -c "import csv;r=list(csv.DictReader(open('/tmp/env/scorecard-HEAD.csv')));\
print(len(r), sum(1 for x in r if not (x.get('comparison_tier') or '').strip()))"
# expect: 624 0
```

If your worktree copy differs from HEAD, that is the appending producer, not new measurement.
`scorecard-fixer` measured one such episode as 6 runs x 28 cells with **zero unique
measurements** — appended rows are re-runs, not coverage.

## 2. Two methodology facts that change the arithmetic

**(a) A total over the whole CSV is not a defined quantity.** From
`compat-envelope/README.md` (schema section):

> The renderer keys logical cells on `(bucket, test_id, test_mode, backend)`. `--all` is
> accepted only for a single run identity; mixed-run aggregation is refused. Use `--run-id`
> (or `--latest`) so a table has one code/population state.

`scorecard.csv` at HEAD is a **cumulative log of 10 run identities** spanning several Hermit
SHAs. So `457 pass / 624 rows = 73%` is not a compat number — it aggregates 10 runs and
several code states, which the renderer explicitly refuses to do. Any envelope figure must
name its run identity.

**(b) Only two `comparison_tier` values count green.** From the same section:

> `full-stdout-info-stack-heap` and `stdout-info-stack-heap-spot-check` are the only values
> that qualify a raw pass as green. `legacy-unqualified`, `unqualified-stdout-only`, and
> `unqualified-tool-count-only` preserve weaker evidence explicitly and never count green.

## 3. The label-level envelope: 6 cells, one bucket, one backend, one run

Across all four scorecards at HEAD:

| CSV | rows | qualifying-tier rows | rest |
| --- | ---: | ---: | --- |
| `scorecard.csv` | 624 | **6** (`full-stdout-info-stack-heap`) | 618 `legacy-unqualified` |
| `fullcorpus-scorecard.csv` | 1200 | 0 | 1200 `legacy-unqualified` |
| `e9patch-scorecard.csv` | 454 | 0 | 454 `legacy-unqualified` |
| `reverie-scorecard.csv` | 12 | 0 | 12 `legacy-unqualified` |
| **total** | **2290** | **6** | 2284 |

Corroborated by the repository's own renderer, which independently selects the same run and
prints the same 6:

```bash
./compat-envelope/render-scorecard.rs --csv /tmp/env/scorecard-HEAD.csv --latest
# comparison-tier distribution: {"full-stdout-info-stack-heap": 6} (6 rows);
#   qualified green=6/6 raw passes
# bucket                  ptrace
# ptrace-short-full-tier       6
# TOTAL                        6
```

**The six, named.** All share: bucket `ptrace-short-full-tier`, backend **`ptrace`**,
`test_mode=verify`, `lane=portable`, `run_mode=expansion`, `cell_state=enabled`,
`outcome=pass`, `deterministic=1`, `tier=bitwise`, `verify_compare=canonical`, `dirty=false`,
`selected/executed/evidence = 6/6/6`, `run_id=ptrace-short-full-tier-first-green`,
Hermit `590fcc9eeb0339c5cf23f72b84394a63333e88ff`, Reverie `6144323c5dab…`.

1. `ptrace-short-full-tier/heapy`
2. `ptrace-short-full-tier/print_memaddrs`
3. `ptrace-short-full-tier/name_to_handle_at_eopnotsupp`
4. `ptrace-short-full-tier/name_to_handle_directory_eopnotsupp`
5. `ptrace-short-full-tier/name_to_handle_empty_path_eopnotsupp`
6. `ptrace-short-full-tier/name_to_handle_regular_eopnotsupp`

**Execution context:** native host execution under the **ptrace** backend, Hermit **strict
verify** run mode (`test_mode=verify` — the golden `--strict` + replay comparison), portable
lane. No DBI, KVM, SaBRe or LiteInst cell qualifies.

**Program category:** four of the six (#3–#6) are distinct C sources under `hermit/tests/c/`
that all exercise `name_to_handle_at` **error paths** (`EOPNOTSUPP` variants: regular file,
directory, empty path, plain `at`). They are separate programs, not duplicate rows, but they
are variants of one syscall behaviour — so "6 programs" overstates behavioural breadth.
`print_memaddrs` is an address-layout guest (`hermit/tests/c/print_memaddrs.c`). `heapy` —
**UNVERIFIED**: I could not locate a source file for it under `hermit/`.

**Why this batch:** it is not a sample of the corpus. It is the single run that commit
`ddfd448 "compat-envelope: earn the first 6 qualified greens — ptrace, short guests, full
tier"` created specifically to produce the first rows at the full tier. Selection was
"shortest guests on the reference backend", i.e. chosen to be provable, not representative.

**Currency:** Hermit `590fcc9e` (2026-08-06) is an **ancestor** of the current parent pin
`f65f7446`, **20 commits behind**. The baseline is on the mainline but not at the tip.

## 4. The evidenced envelope is 0 — the six claims are refused by the repo's own checker

Two checkers exist and they disagree. Only one is wired to anything.

```bash
python3 compat-envelope/check-scorecard-tier.py ; echo "rc=$?"
#  ... qualified green=6/1947 raw passes
#  rc=0                      <-- vocabulary only: is the label in the allowed set?

python3 compat-envelope/tier_evidence.py ; echo "rc=$?"
#  qualifying tier claims : 6 of 2402 rows
#  fully evidenced        : 0 of 6
#  NOT evidenced          : 6 of 6
#  rc=1                      <-- reads the evidence columns
```

(`2402`/`1947` rather than `2290` because these scan the working tree, which was dirty; the
qualifying set is the same 6 either way, since every appended row is `legacy-unqualified`.)

`tier_evidence.py` refuses each of the six with the same three reasons:

```
missing:stdout (stdout_parity is blank)
schema-cannot-express:stack (no 'stack_parity' column)
schema-cannot-express:heap  (no 'heap_parity' column)
```

Component-by-component audit of the six rows, counted directly:

| Component the tier `full-stdout-info-stack-heap` names | Evidence column | Populated |
| --- | --- | ---: |
| stdout | `stdout_parity` | **0 / 6** |
| stdout operands | `output_hash`, `ref_output_hash` | **0 / 6**, **0 / 6** |
| INFO log | `compared_log_messages` (e.g. `150\|150`) | 6 / 6 |
| stack | `stack_parity` | column does not exist |
| heap | `heap_parity` | column does not exist |
| (recorded instead) | `stack_hash`, `heap_hash` | 6 / 6, single operand only |
| exit code / detlog / oracle | `exit_code_parity`, `detlog_parity`, `oracle_verdict` | 0 / 6 each |

So **1 of the 4 named components** (the INFO log) is evidenced on the row. Two distinct
defects, which need different fixes and should not be collapsed:

- **Producer gap (stdout).** `stdout_parity` *and both* SHA-256 operands are blank, so stdout
  agreement is not merely underived — it is unrecoverable from the row.
- **Schema gap (stack/heap).** The rows do carry `stack_hash`/`heap_hash`, so the
  measurement was taken; but there is one operand and no reference, and the checker names
  `stack_parity`/`heap_parity`, columns the schema has never had. Renaming would not fix it —
  a parity needs two operands.

**Nothing enforces this.** `ai_docs/2026-08-07-tier-claims-carry-no-evidence.md` states it
plainly: *"Wiring `tier_evidence.py` into a gate. It is a standalone checker; nothing calls it
yet."* Confirmed by search: no `.sh`, `.py`, `.rs`, `.yml` or `Makefile` outside its own test
file invokes it. So the published `6` comes from the checker that reads only the label, and
the checker that reads the evidence says `0` and never runs.

## 5. Denominators

**Cells by backend** — distinct `(bucket, test_id, test_mode, backend)` in `scorecard.csv` at
HEAD (568 total; 624 rows because 28 cells were re-measured, max 3 times):

| backend | cells | qualifying tier |
| --- | ---: | ---: |
| liteinst | 220 | 0 |
| kvm | 200 | 0 |
| ptrace *(reference)* | 105 | **6** |
| dbi | 36 | 0 |
| sabre | 7 | 0 |
| **total** | **568** | **6** |
| **non-ptrace (cross-backend)** | **463** | **0** |

**Programs by bucket** — distinct `test_id`, with the backends and modes actually seen:

| bucket | programs | backends | modes |
| --- | ---: | --- | --- |
| c-programs | 159 | dbi, kvm, liteinst, ptrace, sabre | verify |
| backend-parity | 28 | dbi, ptrace | strict, verify |
| backend-parity-spst | 20 | liteinst, ptrace | verify |
| determinism-stress-c | 10 | kvm, liteinst, ptrace | verify |
| system-utils | 7 | kvm, liteinst, ptrace, sabre | custom, replay, verify |
| language-runtimes | 6 | kvm, liteinst, ptrace | verify |
| **ptrace-short-full-tier** | **6** | **ptrace** | **verify** |
| determinism-stress | 4 | kvm, liteinst, ptrace | chaos, verify |
| shared-futex-c | 4 | kvm, liteinst | verify |
| backend-parity-c | 3 | kvm, liteinst | verify |
| data-handling | 2 | kvm, liteinst, ptrace, sabre | verify |
| bin-c | 2 | kvm, liteinst | verify |
| applications, chaos-c, debugger-c, util-c | 1 each | (see CSV) | verify |
| **total distinct `test_id`** | **255** | | |

**There is no single agreed corpus denominator.** `corpus-manifest.csv` and `scorecard.csv`
disagree in both directions:

| | count |
| --- | ---: |
| `(bucket, test_id)` in `corpus-manifest.csv` | 235 |
| `(bucket, test_id)` in `scorecard.csv` | 255 |
| in both | 200 |
| in the manifest, **never measured** | 35 |
| measured, **not in the manifest** | 55 |

Quote a coverage ratio only with the denominator named, because 200/235 and 200/255 are both
defensible and they are different numbers.

## 6. Composition of the rest of the file (why 457 "passes" is not 457 greens)

| dimension | breakdown |
| --- | --- |
| `cell_state` | 422 `disabled` (expansion candidate — *not* in the regression envelope), 186 `enabled`, 16 `expansion` |
| `outcome` | 457 pass, 83 fail, 72 skip, 7 unavailable, 5 gap |
| `cell_state` x `outcome` | **275 `disabled`+`pass`**, 174 `enabled`+`pass`, 72 `disabled`+`skip`, 70 `disabled`+`fail`, 8 `expansion`+`pass`, 8 `expansion`+`fail`, 7 `enabled`+`unavailable`, 5 `enabled`+`fail`, 5 `disabled`+`gap` |
| `tier` (self-determinism comparator) | 346 `stripped-uncounted`, 272 blank, 6 `bitwise` |
| `run_mode` | 446 expansion, 178 regression |
| `lane` | 622 portable, 2 privileged |

The largest single group is `disabled` + `pass` (275). Per `README.md`, `disabled` means
"expansion candidate", not part of the regression envelope — a pass there is a candidate for
promotion, not a certified green. Combined with §2(a), a naive `457/624` mixes 10 runs,
disabled candidates, and unqualified tiers.

## 7. Marked UNVERIFIED

Stated rather than estimated, per the standard that a grep hit is a candidate, not an instance.

- **`heapy` source location.** Not found under `hermit/` by name. The other five map to
  `hermit/tests/c/*.c`.
- **Whether the 618 `legacy-unqualified` rows are *substantively* worse or merely
  *unlabelled*.** I verified the label and, for the 6, the evidence columns. I did **not**
  audit the evidence columns of the other 2,284 rows; some may carry operands their tier does
  not claim. "Unqualified" here is a statement about the recorded tier, not a re-measurement.
- **Whether any backend would pass at a qualifying tier if measured.** Zero cross-backend
  cells have been *attempted* at the full tier, so `0/463` is "never measured at this
  standard", **not** "measured and failed". These are different and the scorecard cannot
  distinguish them today.
- **Whether the 35 manifest-only programs are runnable.** Not attempted; this task measures,
  it does not expand.
- **Currency of the other three scorecards.** I read their tier columns and row counts; I did
  not check their run identities or SHAs for staleness.

## 8. Re-derivation

Everything above comes from these commands, in this order, from the dev-hermit root:

```bash
# §1 source
git show HEAD:compat-envelope/scorecard.csv > /tmp/env/scorecard-HEAD.csv   # + the other 4

# §3 label-level count, two independent ways
python3 -c "import csv,collections;r=list(csv.DictReader(open('/tmp/env/scorecard-HEAD.csv')));\
print(collections.Counter(x['comparison_tier'] for x in r))"
./compat-envelope/render-scorecard.rs --csv /tmp/env/scorecard-HEAD.csv --latest

# §4 evidence-level count
python3 compat-envelope/check-scorecard-tier.py ; echo rc=$?     # rc 0, says 6
python3 compat-envelope/tier_evidence.py        ; echo rc=$?     # rc 1, says 0 of 6

# §5 denominators
python3 -c "import csv,collections;r=list(csv.DictReader(open('/tmp/env/scorecard-HEAD.csv')));\
k=lambda x:(x['bucket'],x['test_id'],x['test_mode'],x['backend']);\
d=collections.defaultdict(set);[d[x['backend']].add(k(x)) for x in r];\
print({b:len(v) for b,v in d.items()})"

# §6 composition
python3 -c "import csv,collections;r=list(csv.DictReader(open('/tmp/env/scorecard-HEAD.csv')));\
print(collections.Counter((x['cell_state'],x['outcome']) for x in r))"

# §4 wiring claim
grep -rn tier_evidence --include=*.sh --include=*.py --include=*.rs --include=*.yml . \
  | grep -v compat-envelope/tier_evidence.py
```

## 9. What this means for the improvement program

The envelope is **not** "6 greens, grow it". It is:

1. **0 evidenced cells.** Before any expansion, either the six existing claims must be made
   evidenced, or the tier must be dropped to one the row can support. Growing a label count
   whose evidence checker returns `0 of 6` grows a number nobody can defend — the exact defect
   this session removed from ci-hub.
2. **The cheapest real win is wiring, not measurement.** `tier_evidence.py` exists, has 18
   passing tests, and is called by nothing. Wiring it turns the scorecard's headline from a
   self-declared label into a checked one, and it will read `0` on the day it is wired — that
   is the correct starting number.
3. **The stdout operands are the smallest concrete gap.** Three of four components fail on the
   six rows; `stdout` fails because the producer wrote neither the boolean nor either hash,
   which is a producer change, not a schema change.
4. **Stack/heap needs a schema decision, not a rename.** One hash per row cannot express a
   parity. Either add reference operands or stop naming stack/heap in the tier.
5. **Cross-backend is `0/463` unmeasured, not `0/463` failing.** The first cross-backend cell
   measured at a qualifying tier will be the first real envelope datapoint.

---

*Produced by `main-red-doctor` for task `establish-current-compat-envelope-baseline`,
2026-08-08. Measurement only — no scorecard, collector, or checker was modified.*
