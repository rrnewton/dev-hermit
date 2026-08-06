# Qualifying the backend-parity-c red cells: 139 of 141 pass, with an engagement witness

**Date:** 2026-08-06 · **Task:** `close-gap-cells-round2` · Local, no egress.
**Binary:** `worktrees/audit/hermit/target/release/hermit`,
`hermit 0.2.0 (2026-08-06, gfad50bc75543)`, sha256 `aa5b0705…fe332027`
(verified **unchanged before and after** the sweep).

## Question

`tests/e2e/manifests/backend-parity-c.toml` disables cells for two different reasons, and the
distinction is the whole task:

- **a known limitation** — *"KVM ElfExecutor returns ENOSYS for sendfile(2)"*. Not a gap; the
  backend genuinely cannot do it.
- **no evidence** — *"Not evaluated in the source backend-parity matrix"* / *"the L2 `--verify`
  witness was not recorded"*. **Unknown, not unsupported.** This is the burndown surface.

Classifying by reason text gives **141 no-evidence cells across 78 fixtures**: dbi 66, sabre 75.
(kvm's remaining reds are mostly documented ENOSYS gaps and kvm livelocks on this host; liteinst
does not complete its preload handshake.)

## Method

Each cell is run **individually** against the ptrace golden, using the legacy driver's exact
command shape (`run_matrix.py::hermit_command`): `--strict`, then `--verify --verify-allow both`,
with `--base-env=minimal --max-timeslice=disabled --tmp=/tmp`.

Verdicts are deliberately not collapsed to pass/fail, and **tier is recorded per cell** so a
"pass" states which level it reached:

| verdict | tier | meaning |
|---|---|---|
| `L2-PASS` | L2 | `--verify` held **and** stdout+exit match the golden **and** engagement proven |
| `L1-ONLY` | L1 | `--strict` matches the golden but `--verify` did not hold |
| `DIVERGES` | L0 | stdout or exit differs from the golden — the real finding |
| `UNAVAILABLE` / `BUILD-FAIL` / `GOLDEN-FAIL` / `TIMEOUT` / `UNVERIFIED-ENGAGEMENT` | no-result | never a pass |

### The engagement gate — why a green here is not vacuous

A cell that merely reproduces the golden's output proves the *guest* ran, not that the *backend*
did; both backends can degrade into something indistinguishable from ptrace. So every L2 pass
carries a per-run witness that the backend was actually engaged:

- **dbi** — `reverie-dbi: … rewritten=N` with N>0 (DynamoRIO rewrote code). Measured range across
  the 65 passes: **37–71**, so engagement is substantive, not a token 1.
- **sabre** — `hermit::sabre::fallback: … guest_rpc_observed=true`, i.e. the guest made
  coordinator RPC calls *through the SaBRe plugin*, which a silent ptrace fallback cannot
  produce. All 74 passes also report `ptrace_fallback_sites=0`.

Without this gate the sabre column would have been unfalsifiable: SaBRe is known to be able to
fall back to ptrace silently.

## Results

| backend | no-evidence cells | **L2-PASS (engaged)** | L0 DIVERGES |
|---|---:|---:|---:|
| dbi | 66 | **65** | 1 |
| sabre | 75 | **74** | 1 |
| **total** | **141** | **139** | **2** |

`flip-list.txt` names the 139 qualifying `(fixture, backend)` pairs — 77 fixtures, each backed by
a row in `results.csv` with its tier and engagement witness.

### The two non-passing cells, both run down

**1. `pipe-ipc` / sabre — a real backend bug.** Deterministic across repeats:

```
read: Unknown error 512      # 512 == ERESTARTSYS
```

SaBRe leaks `ERESTARTSYS` to the guest on a pipe read instead of restarting or converting it.
The guest sees a nonsense errno and exits 1 where ptrace exits 0. This is the only genuine
backend divergence in 141 cells, and it is worth its own fix task.

**2. `cpuid-probe` / dbi — NOT a backend bug; a harness flag asymmetry.** The canonical command
passes `--no-virtualize-cpuid` to **ptrace only**, guarded by `name != "cpuid_policy"`. The
CPUID-sensitive fixture here is `cpuid_probe`, a *different* fixture the special case does not
name — so the golden ran with CPUID de-virtualized while dbi ran with it virtualized, and the two
arms were never comparable. Run symmetrically, they agree exactly:

| arm | rc | stdout |
|---|---|---|
| ptrace **with** `--no-virtualize-cpuid` (what the harness used) | 1 | — |
| ptrace **without** it | 0 | identical to dbi |
| dbi | 0 | identical to ptrace |

So the legacy driver's special case has a **name-mapping gap**: it exempts `cpuid_policy` but not
`cpuid_probe`. Fix the exemption and this cell qualifies too.

### A third defect, found on the way

**The schema-v2 manifest lost the legacy driver's per-fixture build flags.** `run_matrix.py`
carries a hardcoded map supplying `-D_GNU_SOURCE` / `-pthread`; the manifest records
`build.cflags` for only **3 of 78** tests. Compiling with the manifest's flags alone fails on any
fixture using a GNU extension — e.g. `mkdtemp` under `-std=c11`:

```
error: implicit declaration of function 'mkdtemp' [-Werror=implicit-function-declaration]
```

**94 of 141 cells needed the fallback.** My first sweep scored those as 94 red cells before I
checked the error text; they are a *missing build flag in the manifest*, not backend gaps. Any
consumer that builds from the manifest alone will hit this.

## Interpretation

The burndown surface was **not** blocked by backend behaviour. 139 of 141 unevaluated cells
already pass at L2 with proven engagement; the manifest simply never recorded the evidence. The
residue is one real SaBRe bug, one harness flag asymmetry, and a manifest that cannot rebuild its
own fixtures.

**These figures are a qualification, not a flip.** Nothing in this artifact edits the manifest —
see *Not done*.

## Not done, and why

- **No manifest edit, no `ci=true`, no ratchet regeneration.** This lane has **no allocated slot**
  (`worktrees/ACTIVE.md` has no `hermit-w10` row) and slot provisioning is coordinator-only, so
  mutating hermit would be an unauthorized change to a shared surface. `flip-list.txt` makes the
  edit mechanical for whoever holds a slot; `ci/expected-e2e-plan.json` must be regenerated in the
  same change or the plan node exits 1.
- **kvm and liteinst were not measured.** kvm livelocks on this host and *ignores SIGTERM* (a
  prior session needed SIGKILL after 10m at 100% CPU), so budgeting it with `timeout` alone leaks
  a spinning core. liteinst does not complete its preload handshake here.
- **Only the `verify` mode of `backend-parity-c`** — other buckets and modes are untouched.

## Limitations

- One host, one binary, one run per cell: these are **not** flake-tested. A cell that passes once
  is qualified, not proven stable.
- The engagement witness proves the backend *ran*; it does not prove it exercised the syscall the
  fixture targets.
- The golden is ptrace on the same binary, so a defect shared by ptrace and the arm is invisible
  to this comparison by construction.
- Verdicts come from stdout+exit and `--verify`, matching the manifest's declared `observation`
  (`status`, `stdout`); stderr is not compared.

## Reproduction

```sh
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
./sweep_cells.py            # writes cell-qualification.csv
```

Requires a hermit built **with** the `dbi` and `sabre` features. **Run the binary in place** —
hermit discovers the DBI runtime as a *sibling* of the executable, so a copied binary silently
reports `backend 'dbi' is unavailable` and every cell reads as divergent. My first sweep did
exactly that and produced 141 false reds; the harness now classifies that string as
`UNAVAILABLE` (a no-result) rather than a divergence.
