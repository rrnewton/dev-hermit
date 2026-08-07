# Newly-green cell falsifiability, and the scorecard's real denominators

**Task:** `prove-newly-green-cells-can-fail-mutation-pass` · **Agent:** hermit-cc (opus-5) · **2026-08-07**
Every list below is derived from source. Every number carries its denominator.

---

## 1. Newly-green cells: zero

Authority: `compat-envelope/scorecard.csv` — 618 rows, **562** unique `(test_id, test_mode, backend)` keys.
"Newly green" derived by diffing the CSV between its previous commit `7080d68` and `HEAD` on that triple:

> **0 of 562 cells flipped green.**

The most recent scorecard commit (`7bbb34a`, "make tier evidence reach the consumer, and make the gate
bite") changed tier/gate wiring, not outcomes. The standing instruction — mutation-test cells *as* they
flip — has nothing outstanding at this moment.

## 2. The denominators

```
618 rows  ->  outcome=pass: 451
    tier=stripped-uncounted:  346    already ruled unfalsifiable and tiered OUT of counting
    COUNTED green:            105    31 distinct test_ids, lane=portable 105/105
        backend:         dbi 78, ptrace 27
        test_mode:       strict 102, chaos 1, custom 1, replay 1
        verify_compare:  '' on 105/105
        deterministic=1: 0 of 105
```

### The structural finding: there is not one counted determinism claim

All 105 counted greens are **single-run** modes. `compat-envelope/check-determinism-earned.sh` states the
rule: `strict`, `chaos`, `custom` and `replay` execute one run and therefore *cannot* observe `run1==run2`;
they must record BLANK determinism. They do — correctly, all 105.

So "counted green" currently means only *"the guest ran and exited as expected under one strict run."*
That is a real, falsifiable claim, but far weaker than the word green implies, and the scorecard's entire
determinism evidence now sits inside the 346 `stripped-uncounted` rows that were already ruled
unfalsifiable.

## 3. The count that failed to fail: 4 of 75 (5.3%)

The landed corpus was already swept by `experiments/fixture-can-fail-sweep_20260806/`. Its denominator was
verified against the tree rather than taken on trust: **75** `.c` files in
`hermit/tests/backend-parity/fixtures`, matching its claim.

| verdict | count | share |
|---|---:|---:|
| CAN-FAIL | 71 | 94.7 % |
| — caught by status *and* stdout | 40 | |
| — caught by **stdout only** | **31** | |
| OBSERVATION-ONLY (no standalone oracle) | **4** | 5.3 % |
| CANNOT-FAIL | 0 | 0 % |

**The live risk is the 31, not the 4.** Those fixtures' exit status *never moves* under a planted
violation — only their stdout changes. Any consumer gating on exit status alone is blind to **31 of 71
(44 %)** of the fixtures that can otherwise fail. The 4 observation-only fixtures (`kcmp_refusal`,
`no_new_privs_refusal`, `openat2_refusal`, `pid_probe`) are worth nothing unless the cross-backend stdout
comparison is actually wired.

## 4. Independent both-direction spot check

Not a relay of the sweep's numbers. Fixture names were derived from `results.csv` and the directory —
after three guessed names turned out not to be real files.

| fixture | clean rc | mutant rc | caught | sweep said |
|---|---:|---:|---|---|
| `kcmp_refusal` | 0 | 0 | **NOTHING** | OBSERVATION-ONLY ✔ confirmed |
| `openat2_refusal` | 0 | 0 | **NOTHING** | OBSERVATION-ONLY ✔ confirmed |
| `aio_refusal` | — | — | mutant did not compile | CAN-FAIL — **not tested** |
| `bind_getsockname` | — | — | mutant did not compile | CAN-FAIL — **not tested** |

The half that matters most reproduced independently: the observation-only fixtures genuinely cannot fail
standalone, 2 of 2 tested. The CAN-FAIL half did **not** reproduce, because my mutation operator (negate
the first `if`) is cruder than the sweep's (negate the first *contract check*, which distinguishes three
code shapes). That is a limitation of my probe, not evidence about those fixtures, and it is counted
neither way.

All fixtures restored; `hermit/tests/backend-parity/fixtures` left 0 dirty.

## 5. What was deliberately not done

No cell was reverted to not-green. None newly flipped, and the standing population already has a
documented sweep. Whether the 4 observation-only cells should be demoted turns on whether the
cross-backend stdout comparison is wired — a different question from falsifiability, and a call for the
owner of that comparison rather than a unilateral edit here.

## 6. Reproduction

```bash
cd ~/work/dev-hermit
# newly-green derivation
python3 - <<'PY'
import csv, io, subprocess
def load(ref):
    t=subprocess.run(['git','show',f'{ref}:compat-envelope/scorecard.csv'],capture_output=True,text=True).stdout
    return {(r['test_id'],r['test_mode'],r['backend']):r for r in csv.DictReader(io.StringIO(t))}
prev,cur = load('7080d68'), load('HEAD')
G=lambda r: r['outcome']=='pass' and r.get('tier','')!='stripped-uncounted'
print(len([k for k,v in cur.items() if G(v) and not (k in prev and G(prev[k]))]))
PY
# denominators
python3 -c "import csv,collections;rows=list(csv.DictReader(open('compat-envelope/scorecard.csv')));\
p=[r for r in rows if r['outcome']=='pass'];print(len(rows),len(p),collections.Counter(r.get('tier','') for r in p))"
# prior sweep verdicts
python3 -c "import csv,collections;print(collections.Counter(r['verdict'] for r in csv.DictReader(open('experiments/fixture-can-fail-sweep_20260806/results.csv'))))"
```
