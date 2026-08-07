# NOT-COMPARABLE against the published scorecard: the 5 bad cells are not published, and the caveat that hides them is stale

**Task:** `apply-not-comparable-to-the-published-scorecard` · hermit-w11 (`[impl agent, opus-5]`) ·
**2026-08-07** · local, no egress, **read-only against `compat-envelope/`**.
**Upstream producer:** `self-determinism-precondition-check-every-backend-every-dimension`
(hermit-w2, CLOSED) — its 12-cell matrix is taken as given here and re-quoted verbatim in §1.

---

## 0. The headline, because it changes what this task should do

**No parity percentage is standing on any of the 5 NOT-COMPARABLE cells.** Every parity figure
in every published scorecard is the **stdout** dimension, and stdout is one of the 7
self-deterministic cells on all three measured backends. The 5 bad cells live on
`detlog`/`stack`/`heap` — dimensions the published artifact **never emits a parity figure for at
all**.

So the literal instruction — "do not leave a parity percentage standing for them" — is already
satisfied, but **by omission, not by design**. Nothing in the emission path is fail-closed: the
day someone wires deeper-dimension parity, a figure computed against a nondeterministic side
will be emitted with nothing to stop it. That, plus a now-stale caveat, is the real remaining gap
and is what §3 patches.

**This artifact does not modify `compat-envelope/`.** Those files carry another agent's
uncommitted work (see §4); the change is specified here as an exact patch and handed off.

---

## 1. The 12-cell matrix (from the producer, verbatim)

Guest `heapy` (static, real malloc churn — chosen because it yields nonzero heap *and* stack).
Two runs per backend, identical invocation, ordinals compared position-by-position after
stripping only the wall-clock prefix. All 6 runs rc=0.

| backend | dimension | run1 | run2 | match/denom | verdict |
| --- | --- | --- | --- | --- | --- |
| ptrace | stdout | 1 | 1 | 1/1 | SELF-DETERMINISTIC |
| ptrace | detlog | 108 | 108 | 108/108 | SELF-DETERMINISTIC |
| ptrace | stack | 26 | 26 | 26/26 | SELF-DETERMINISTIC |
| ptrace | heap | 24 | 24 | 24/24 | SELF-DETERMINISTIC |
| kvm | stdout | 1 | 1 | 1/1 | SELF-DETERMINISTIC |
| kvm | detlog | 104 | 104 | 104/104 | SELF-DETERMINISTIC |
| **kvm** | **stack** | 0 | 0 | **0/0** | **NOT-COMPARABLE — vacuous n=0** |
| **kvm** | **heap** | 0 | 0 | **0/0** | **NOT-COMPARABLE — vacuous n=0** |
| dbi | stdout | 1 | 1 | 1/1 | SELF-DETERMINISTIC |
| **dbi** | **detlog** | 84 | 84 | **4/84** | **NOT-COMPARABLE — divergence** |
| **dbi** | **stack** | 26 | 26 | **0/26** | **NOT-COMPARABLE — divergence** |
| **dbi** | **heap** | 0 | 0 | **0/0** | **NOT-COMPARABLE — vacuous n=0** |

**The counts sum, with no cell dropped:**

```
12 cells measured
 =  7 SELF-DETERMINISTIC  (parity-emittable)
 +  5 NOT-COMPARABLE      = 2 by divergence (dbi detlog, dbi stack)
                          + 3 by n=0        (kvm stack, kvm heap, dbi heap)
```

Per backend: ptrace 4/4 emittable · kvm 2/4 · dbi 1/4. Per dimension: stdout 3/3 · detlog 2/3 ·
stack 1/3 · heap 1/3.

**Unmeasured, and not passing:** sabre and liteinst — **2 of 5 backends, 0 of 8 dimension-cells**.
A 20-cell matrix is the real denominator; 12 is what exists. Tracked by
`self-determinism-sweep-sabre-and-liteinst`.

### The dispatch's KVM reason is wrong — carry the measured one

Both this task and its parent state *"KVM stack is the clearest failure — 21/31 ordinals differ
between its own runs."* The producer measured **0 of 0**: KVM emitted **zero** stack and **zero**
heap records on that guest and binary. That is not 21/31 differing, it is **nothing emitted**.
Both readings end at NOT-COMPARABLE, but the reasons are different failures with different fixes
— divergence means *fix the backend*, n=0 means *the probe never fired*. **The reason that ships
must be `vacuous n=0`.** Whether 21/31 came from a different guest, binary, or a since-fixed
defect is unresolved and is not assumed either way here.

---

## 2. What the published scorecard actually emits — measured, all four CSVs

| CSV | rows | parity figures | `bitwise_parity` | `compared_log_messages` | columns naming stack/heap/detlog |
| --- | --- | --- | --- | --- | --- |
| `scorecard.csv` | 618 | 514 | 0 | 0 | **NONE** |
| `fullcorpus-scorecard.csv` | 1200 | 924 | 0 | 0 | **NONE** |
| `e9patch-scorecard.csv` | 454 | 227 | 0 | 0 | **NONE** |
| `reverie-scorecard.csv` | 12 | 6 | 0 | 0 | **NONE** |
| **total** | **2284** | **1671** | **0** | **0** | **NONE** |

`collect-envelope.rs` computes parity by hashing **`out.stdout` only**; a grep of the parity path
finds zero occurrences of `--log`, `--detlog-stack` or `--detlog-heap`. `tier` never takes a
DETLOG-bitwise value in the shipped data — only `stripped-uncounted` (346 rows) or blank (272).

**Therefore: 1671 published parity figures, 0 of them on a NOT-COMPARABLE cell.**

### The caveat that currently stands, and why it is now false

`render-scorecard.rs:741` prints, on every render:

> CAVEAT: stdout-parity% compares piped guest stdout SHA-256 only. It is an upper bound on
> four-signal cross-backend parity; INFO logs, stack detlogs, and heap detlogs **are not
> measured**. TTY behavior is also outside this scorecard.

"Are not measured" **was** true and is now stale. They *have* been measured, and the result is
worse than unmeasured for 5 cells: two of them are measured-and-nondeterministic. A reader today
is told the deeper signals are simply absent, which reads as "unknown, probably fine" — when what
is known is that DBI's detlog agrees with itself **4 times in 84** and its stack **0 times in 26**.
Understating a known negative as an absence is the same failure shape the parent task was filed
against.

### One honest weakness in the clean bill of health

Stdout self-determinism is **1/1** on **one** guest. A denominator of one ordinal is thin
evidence to bless 1671 published figures. It is not wrong, but it is not strong either, and it
should not be quoted as "stdout is proven self-deterministic" without that denominator attached.
Widening it is cheap and belongs with the sabre/liteinst extension.

---

## 3. The exact change to apply (handoff — not applied here)

**File:** `compat-envelope/render-scorecard.rs`, the `--observable stdout` caveat at line 741.

Replace the trailing *"...are not measured. TTY behavior..."* with a statement that carries the
measurement and its reasons:

```
CAVEAT: stdout-parity% compares piped guest stdout SHA-256 only. It is an upper bound on
four-signal cross-backend parity. The other three signals are NOT unmeasured — they were
measured for self-determinism (task self-determinism-precondition-check-every-backend-every-
dimension, guest heapy, 2 runs/backend) and 5 of 12 backend x dimension cells are
NOT-COMPARABLE: dbi detlog 4/84 ordinals and dbi stack 0/26 (divergence, one side differs from
itself); kvm stack 0/0, kvm heap 0/0, dbi heap 0/0 (vacuous n=0, nothing emitted). No parity
figure may be emitted for those 5. sabre and liteinst are unmeasured (0 of 8 cells). TTY
behavior is also outside this scorecard.
```

**And make the omission fail-closed**, so this cannot regress into a real published number.
Deeper-dimension parity is not wired today; the guard must exist *before* it is:

- Add a `self_determinism` gate keyed on `(backend, dimension)` with three states —
  `SELF_DETERMINISTIC{matched,denom}` / `NOT_COMPARABLE{reason, matched, denom}` / `UNMEASURED`.
- Any emitter of a parity figure for a `(backend, dimension)` must consult it and **refuse** to
  emit for `NOT_COMPARABLE` or `UNMEASURED`, rendering the literal cell `NOT-COMPARABLE` with its
  reason (which side, which dimension, ordinals-matching) rather than a percentage.
- `UNMEASURED` must refuse too. If it defaults to permit, sabre and liteinst — the 8 cells nobody
  has measured — silently become emittable, which is exactly the hole this task exists to close.
- Bracket both sides, per the Proxy Binding rule: plant a `NOT_COMPARABLE` cell and confirm the
  renderer prints `NOT-COMPARABLE` with the reason and **no** percentage; plant a
  `SELF_DETERMINISTIC` cell and confirm the figure still fires. State both counts.

**Do not** stamp NOT-COMPARABLE on the existing stdout rows. Those cells are comparable — stdout
is self-deterministic on all three measured backends — and mislabelling them would destroy 1671
legitimate figures to fix a problem they do not have.

---

## 4. Why this was not applied directly

`compat-envelope/` currently carries **uncommitted** changes to 8 tracked files, including both
files this patch touches (`render-scorecard.rs`, `collect-envelope.rs`) plus `scorecard.csv`
itself — an in-flight schema widening (`stdout_parity`, `parity_exercised`, `backend_engaged`,
`native_output_hash`, `ref_output_hash`, `run_flags`) associated with the
`outer_scorecard_schema_skew` workstream. That diff adds **no** NOT-COMPARABLE or
self-determinism handling, so the two changes do not conflict in intent — but editing and
committing a shared dirty parent path would sweep another agent's work into this commit, which is
the documented way landed lines get silently reverted.

The patch in §3 is written against the post-schema-change shape and should be applied by whoever
lands that work, or by this task once the tree is clean.

---

## Reproduction

```bash
# published-surface audit (§2)
python3 - <<'EOF'
import csv,glob
for f in sorted(glob.glob('compat-envelope/*scorecard*.csv')):
    rows=list(csv.DictReader(open(f)))
    print(f, len(rows),
          sum(1 for r in rows if r.get('parity','').strip() or r.get('stdout_parity','').strip()),
          [k for k in rows[0] if any(d in k.lower() for d in ('stack','heap','detlog'))])
EOF

# the caveat as published
compat-envelope/render-scorecard.rs --csv compat-envelope/scorecard.csv --all
```

Producer matrix: `tg show self-determinism-precondition-check-every-backend-every-dimension`.
