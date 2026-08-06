# Headline numbers, restated with provenance

**All values re-derived 2026-08-06.** Every figure carries a denominator and a
reproduce command. Three of the six were **wrong or misleading as sent**; those
are marked and corrected. Nothing here is quoted from memory.

Reference points used throughout: HERMIT main `4c70658e7`, PARENT main moved
during the session (different repos — never mix them).

---

## 1. Implemented-but-unlanded — **CORRECTED (number moved)**

| | |
| --- | --- |
| **Value** | **256** tasks |
| **Denominator** | tasks with `status=IN_PROGRESS` **AND** tag `implemented`. **Not** the 897 total `implemented`-tagged — 633 of those are `CLOSED` and went through the close-task gateway. |
| **Reproduce** | `tg sql "SELECT COUNT(*) FROM tasks WHERE status='IN_PROGRESS' AND tags LIKE '%implemented%'"` |
| **Conditions** | Grows continuously as agents tag work. |

**Correction:** I reported **209** earlier today. It is **256** now — the count
grew by 47 within the session. The 209 was correct when taken; it is not a fact
to requote. **Re-derive before every use.**

**What it does *not* mean:** an audit of the 209 classified them as 163 LANDED
(ancestry-confirmed) / 24 unverifiable / 16 artifact-only / 4 missing-artifact /
**1 genuinely NOTHING**. So "implemented-unlanded" is a queue depth, **not** a
count of unbacked claims.

---

## 2. Bitwise parity 0/346 — **CORRECTED (label was wrong, number was right)**

| | |
| --- | --- |
| **Value** | **0 bitwise**, of **346** cells carrying a determinism verdict, out of **618** scorecard rows |
| **Denominator** | 346 = rows with `deterministic=1`; **all 346** are `verify_compare=stripped`. The other 272 rows carry no determinism verdict at all. |
| **Reproduce** | `grep -c ',stripped$' compat-envelope/scorecard.csv` and `python3 -c "import csv,collections;r=list(csv.DictReader(open('compat-envelope/scorecard.csv')));print(collections.Counter(x.get('verify_compare','') for x in r))"` |
| **Conditions** | `stripped` normalises addresses and tmp paths and **does not compare the detlog**. |

**Correction:** the *number* 0/346 is right; the *label* was not. 346 is a count
of **determinism verdicts under the stripped comparator**, not of parity
comparisons. Parity is a separate column, since renamed `stdout_parity`
precisely because it compares **stdout only**. Read as "we have 346 parity
results and 0 are bitwise", it overstates what was measured — there were never
346 parity measurements to be bitwise.

---

## 3. Per-backend compat % — **CORRECTED (one figure is materially wrong)**

| backend | pass / enabled | % | reading |
| --- | --- | --- | --- |
| `ptrace` | 79 / 79 | **100%** | sound |
| `dbi` | 86 / 87 | **98.9%** | sound |
| `kvm` | 3 / 7 | **42.9%** | sound — 4 real `fail` |
| `sabre` | 0 / 7 | ~~0%~~ | **WRONG — see below** |

| | |
| --- | --- |
| **Denominator** | cells with `cell_state=enabled` (180 of 618). The other 438 are `disabled` (422) or `expansion` (16) and are **not** in any percentage. |
| **Reproduce** | `python3 -c "import csv,collections;r=[x for x in csv.DictReader(open('compat-envelope/scorecard.csv')) if x['cell_state']=='enabled'];d=collections.defaultdict(lambda:[0,0]);[ (d[x['backend']].__setitem__(1,d[x['backend']][1]+1), x['outcome']=='pass' and d[x['backend']].__setitem__(0,d[x['backend']][0]+1)) for x in r];print({k:(v[0],v[1]) for k,v in d.items()})"` |

**Correction — do not use "SaBRe 0%".** All 7 sabre cells have
`outcome=unavailable`, reason **"backend binary not present in this checkout"**.
They are **not failures**. 0% says the backend is totally broken; the truth is it
was **never run** in that collection. SaBRe demonstrably *works* — measured
separately the same day, it executes guests and intercepts syscalls. The honest
cell is **UNKNOWN (0 measured)**, not 0%.

---

## 4. Branches needing rebase — **0 of 42, but the population changed**

| | |
| --- | --- |
| **Value** | **0** of **42** open PRs have a head that does not already contain main |
| **Denominator** | 42 open PRs on `rrnewton/hermit`; all 42 heads resolved locally, merge-base `4c70658e7` for every one |
| **Reproduce** | `gh pr list --repo rrnewton/hermit --state open --limit 100 --json number,headRefOid` then per head: `git -C hermit merge-base --is-ancestor 4c70658e7 <head>` |
| **Conditions** | hermit is a **SHALLOW** clone; ancestry beyond the shallow boundary is unreliable. Every head here resolved, so this reading is sound for this population. |

**Why this looks like it contradicts the earlier "0–90 behind, median 25":** it
does not — **the population changed**. 23 of the 42 open PRs were opened *today*
from the current tip, and main has not moved since (`4c70658e7`). The earlier
figure described a different, older set. A percentage over a churning population
is not comparable across the day.

---

## 5. Fixture coverage — **11 tests, none landed**

| | |
| --- | --- |
| **Value** | **11** new fixture tests: 5 process-tree ordering + 6 signal inheritance |
| **Denominator** | `#[test]` functions in the two new files. **Not** a coverage percentage of anything — there is no denominator of "fixtures needed". |
| **Reproduce** | `git -C hermit show c25549b5e:detcore/tests/misc/process_tree_ordering.rs \| grep -c '^#\[test\]'` and `git -C hermit show dd9d8569c:detcore/tests/misc/signal_inheritance.rs \| grep -c '^#\[test\]'` |
| **Conditions** | Both are **open drafts** (PR #1704, #1708), **not merged**, and both stacked on unmerged #1693. All 11 pass locally at L0, ptrace, no relaxations. |

**Care needed:** "fixture coverage" invites a percentage. There is no defensible
denominator, so quote the **count of landed fixtures — currently 0** — and the
count written separately.

---

## 6. Green-time % — **0.41%, and the number is nearly meaningless as stated**

| | |
| --- | --- |
| **Value** | **0.41%** green |
| **Denominator** | 110.55 h of wall-clock window = 0.46 h green + 17.91 h red + 2.25 h no_result + **89.94 h GAP** |
| **Reproduce** | `python3 ci-hub/history/query.py green-time` |
| **Conditions** | Authoritative check `CI (GitHub-managed portable)`, definition 2026-08-04. Ledger-corroborated green is **0.0%**; the 0.41% is **conclusion-only**. |

**Read the denominator before quoting this.** **81% of the window (89.94 h of
110.55 h) is GAP — no data at all.** "0.41% green" sounds like the tree is
almost always broken; what it actually says is that we have almost no
observations. Red is 17.91 h, so of the **20.6 h where anything was observed**,
green is ~2.2%. Both framings are defensible; the bare 0.41% is not, because it
silently divides by 90 hours of silence.

---

## Summary of corrections

| number | as sent | corrected |
| --- | --- | --- |
| implemented-unlanded | 209 | **256** today; a queue depth, not unbacked claims (1 of 209 was truly NOTHING) |
| bitwise parity | "0/346 bitwise parity" | number right, **label wrong** — 346 is stripped-comparator determinism, not parity |
| SaBRe compat | 0% | **UNKNOWN (0 measured)** — 7 cells are `unavailable`, not failures |
| branches needing rebase | 0–90 behind, median 25 | **0 of 42** — different population, not a contradiction |
| fixture coverage | — | 11 written, **0 landed** |
| green-time | 0.41% | 0.41% of a window that is **81% no-data** |

**Nothing in this table is UNKNOWN-for-lack-of-effort;** the one UNKNOWN
(SaBRe compat) is unknown because the cells were never executed, which is itself
the finding.
