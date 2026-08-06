[impl agent, claude-opus-5]

Standalone off `main`. Follow-up to the SaBRe connect crash: that was fixed by a rebuild; this stops the next one costing a diagnosis.

## Summary

`libdetcore_sabre.so` is a separate Cargo artifact that lands in the **same target directory** as `hermit`, so changing `Config` — or merely switching branches — leaves it stale while everything still *looks* built. `Config` crosses the wire during the handshake, so a stale plugin decodes it against the wrong layout.

**The damage was never the staleness. It was the diagnosis cost.** One added `bool` field surfaced as:

```
failed to connect Detcore SaBRe plugin to coordinator: Decode(InvalidBooleanValue(20))
```

An error that names no version, points nowhere near the plugin, and **blocked every SaBRe measurement** until someone guessed. The DBI backend already fails loudly and specifically when its runtime is missing (`Hermit DBI runtime was not built beside …`); this brings SaBRe to that standard.

## The guard

The coordinator publishes a fingerprint of its `Config` shape next to the RPC socket it already publishes; the plugin compares **before** connecting, so the mismatch is reported rather than re-encountered as a codec error a few frames later.

```
Detcore SaBRe plugin/coordinator MISMATCH: this plugin was built from a Config whose
shape is a927db265cca71b8, the coordinator expects d0fbc946b5e0dcbd. The plugin is a
separate artifact in the same target directory and is stale -- rebuild it against this
coordinator: cargo build -p detcore-sabre
```

**Why this fingerprint.** It is the JSON encoding of `Config::default()`, hashed — every field name and default value. That is a *direct reading of the struct definition*, not a proxy: adding, removing, renaming, reordering or retyping a field all change it. A git SHA or build timestamp would be a proxy — it would flag rebuilds that changed nothing and, worse, could match across two trees that genuinely differ.

It is deliberately **stricter** than the wire format requires: bincode is positional, so a pure rename would not break decoding, yet it changes the fingerprint. Refusing there is the safe direction — a false mismatch costs one rebuild; a missed one costs the outage this exists to prevent.

**A coordinator that publishes no fingerprint is reported and allowed to proceed**, not refused. Rejecting pairs that are actually fine would make the guard worse than not having it.

## Verified both ways, by planting the original outage

Reproduced the exact failure: added a `Config` field, rebuilt **only the plugin** against it, reverted the field, rebuilt **only the coordinator**.

| | result |
| --- | --- |
| **Stale plugin** | **refused**, naming both shapes and the remedy; guest never started; **no `InvalidBooleanValue`** |
| **Matched pair** (before) | `matched-pair-ok`, `EXIT=0` |
| **Matched pair** (after rebuilding the plugin) | `recovered`, `EXIT=0` |

Exact command: `HERMIT_SABRE_BINARY=<sabre> ./target/debug/hermit --backend sabre run --strict -- /bin/echo …`

## Determinism

No guest-visible behaviour changes. The fingerprint is computed from `Config::default()`, a compile-time constant of the build, so it is identical on every run of a given binary and cannot introduce run-to-run variation. The comparison happens once, before the guest starts, and its only outcomes are proceed or refuse — it cannot alter a run that proceeds.

## Validation

**Head:** `01c6f6aec6780a83511ce0765e11107fc9a5591c` · **Base:** `origin/main` `4c70658e785834737cbe1524f77330c781a6f5ea` (0 behind, 1 ahead)

| Check | Result |
| --- | --- |
| Stale plugin refused and named | yes — see above |
| Matched pair still connects | yes, before and after |
| `cargo test -p detcore-model` | **55 passed, 0 failed** (1 new) |
| `cargo fmt --all -- --check`, `cargo clippy -p detcore-model -p detcore-sabre` | clean |
| Builds | `detcore-sabre`, and `hermit --features third-party-backends` |

The new unit test pins both halves: the fingerprint is **stable** (a build agrees with itself — the property that keeps matched pairs working) and **shape-sensitive** (an added field, a removed field, and a pure rename each change it).

**A defect I introduced and caught:** my first insertion landed between an existing `#[should_panic]` attribute and its function, silently stealing it — my test became `should_panic` and `validate_rejects_unrepresentable_chaos_slowdown_factor` lost its guard. Caught by the failing run, attributes restored, verified the neighbour still carries its `should_panic`.

## Not claimed

Only the SaBRe plugin is guarded. The DBI runtime already fails loudly on absence but is **not** fingerprinted, so a stale-but-present `libdetcore_dbi` would still mismatch silently; the same applies to LiteInst. Extending the fingerprint to those is the obvious follow-up and is not done here.
