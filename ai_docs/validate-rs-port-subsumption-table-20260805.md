# validate.rs Port — Gate Subsumption Table (Landing Gate)

**Date:** 2026-08-05 · **Author:** hermit-coord (opus-4.8, SOLE VALIDATE PRODUCER)
**PR:** rrnewton/hermit#1635 · **Branch head:** `2fe02e8f08bba5f54b4d9b6022f4e53db68e3348`
**Base:** `d53550510` · **Purpose:** prove the Rust `validate.rs` port drops **no** `run_check` gate — a silently dropped gate is a fake green in the one tool everything else trusts.

## Method — enumerated from SOURCE, not a note

A hardcoded gate list of a growing set has drifted repeatedly. This table is regenerated from `validate.sh` directly:

```
grep -nE 'run_check(_with_timeout)?[[:space:]]' validate.sh | grep '"'   # 61 invocations
```

**Drift trap paid here:** the first regex `run_check_with_timeout[[:space:]]+"` missed the `run_check_with_timeout <N> "..."` numeric-arg form (super-diagnostic suite L4437–4503 + liteinst gates L4591/4594/4600) — a 41-vs-61 undercount. Any future audit MUST use the arg-agnostic pattern above.

### Census of 61 invocations
- **1 non-gate:** L1888 `run_check_with_timeout "$GATE_TIMEOUT_SECONDS" "$@"` — the `run_check` wrapper implementation itself.
- **1 DEAD:** L2184 "Real backend compatibility matrix" in `run_full_backend_gates` — **zero callers** (`grep -n 'run_full_backend_gates'` returns only the definition). No profile runs it.
- **59 live gates** distributed across profiles below.

## Producer routing (Makefile, authoritative)

| Profile | Producer | Makefile |
|---|---|---|
| `full` (`make validate`) | **`./scripts/validate.rs full`** | Makefile:83-84 |
| `--sabre-compat-only` | `./validate.sh` | Makefile:227 |
| `--liteinst-compat-only` | `./validate.sh` | Makefile:230 |
| `--e9patch-compat-only` | `./validate.sh` | Makefile:233 |
| `privileged` / `--privileged-only` | `./validate.sh` | (not repointed) |
| `portable-strict-compat-only` | `./validate.sh` | (not repointed) |

**Only `full` is repointed to validate.rs.** Everything else still invokes `validate.sh`, whose gate set is unchanged on this branch (`git diff d53550510 HEAD -- validate.sh` = **33 additions, 0 deletions, 0 run_check lines touched**; the +33 is the `purge_zero_byte_objects` artifact-integrity preflight).

## Subsumption — the 5 named profiles

Bootstrap gates run ALWAYS (top-level, before any dispatch branch): **G1** "Initialize repository submodules" (L4533), **G2** "Reverie pin consistency" (L4536).

### `full` — producer validate.rs (must subsume validate.sh `run_full_suite`)

`run_full_suite` (validate.sh L4399) = `run_ci_manifest_lane portable` + `run_ci_manifest_lane privileged`; each lane = manifest (L4178) + DAG lane (L4179). Deduped effective set = 6.

| # | Gate (validate.sh source) | validate.rs coverage |
|---|---|---|
| G1 | Initialize repository submodules (L4533) | `run_full_profile` L672 ✓ |
| G2 | Reverie pin consistency (L4536) | L692 ✓ |
| G3 | Centralized test manifest and inventory (L4178, runs 2× dedup 1) | L708, run ONCE ✓ |
| G4 | portable CI DAG lane (L4179) | portable `run_dag_lane` ✓ |
| G5 | privileged CI DAG lane (L4179) | privileged `run_dag_lane` ✓ |

**Verdict: EXACT PARITY.** validate.rs `full` runs all 6, in order, no more, no fewer.

### `privileged` — producer validate.sh (unchanged)
`run_privileged_validation` (L4381) = `run_ci_manifest_lane privileged`: G1, G2, G3 manifest (L4178), G5 privileged lane (L4179). **Covered by validate.sh, unchanged.**

### `portable-strict-compat-only` — producer validate.sh (unchanged)
`STRICT_COMPAT_ONLY` branch (L4573): G1, G2, "Build release Hermit for strict compatibility" (L4576, conditional on default bin) + `run_strict_compatibility_envelope` (L3875, **probe-based, contains no `run_check`**). **Covered by validate.sh, unchanged.**

### `--liteinst-compat-only` — producer validate.sh (unchanged)
`LITEINST_COMPAT_ONLY` branch (L4590): G1, G2, "Build release Hermit for LiteInst compatibility" (L4591), "Build release LiteInst runtime" (L4594), "Portable CI liteinst_strict" (L4600). **Covered by validate.sh, unchanged.**

### `--sabre-compat-only` — producer validate.sh (unchanged)
`SABRE_COMPAT_ONLY` branch (L4609): G1, G2, "SaBRe artifacts configured" (L4610), "Build release Hermit and Detcore plugin for SaBRe compatibility" (L4612), "SaBRe compatibility ratchet" (L4616). **Covered by validate.sh, unchanged.**

## Profiles outside the 5 named (completeness — all stay on validate.sh, unchanged)
- `quick`: 8 gates L4388-4396 (`run_quick_suite`).
- `super`: `run_super_suite` (L4507) build+determinism gates L4511-4527 + `run_super_diagnostic_suite` (L4436) ~19 diagnostic gates L4437-4503.
- `--only`: DAG shard (L4546).
- `--selective`: `run_selective_suite` (L4286/4288) + `run_exact_detcore_cases` (L4316).
- `--e9patch-compat-only`: L4625/4627/4631.
- `--rr-compat-only`: L4640/4643.
- `--qemu-l2-only`: L4654/4657.
- envelope-only: L4666.

## Landing conclusion

1. **validate.rs owns only `full`** and covers its 6 gates **exactly**.
2. **Every other profile is untouched by the port** (validate.sh: 0 `run_check` deletions) and still routed to validate.sh by the Makefile — so all their gates are covered identically to base main.
3. **No silent drop is possible:** validate.rs's non-`full` path resolves `ci/dag/<profile>.json` and exits 2 (fail-closed) if the file is absent (only `portable.json` + `privileged.json` exist), so it cannot silently run a reduced gate set for a compat profile.

**The `full` repoint is SAFE to land.** `validate.sh` must stay alive (retained, not deleted) as the producer for all non-`full` profiles.
