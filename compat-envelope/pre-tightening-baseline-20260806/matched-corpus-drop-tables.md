## Cell accounting — zero ambiguous missing rows

| class | cells | disposition |
| --- | ---: | --- |
| nominal corpus | 235 | as listed in `corpus/corpus-c.tsv` + `corpus-nonc.tsv` |
| missing guest fixtures (`performance/*`) | 30 | NO-RESULT: source absent at the measured SHA, no row emitted |
| **effective corpus** | **205** | rows emitted, x5 backends = 1025 |
| ptrace reference unusable — verify non-pass | 22 | NO-RESULT for parity on every candidate backend |
| ptrace reference unusable — verify PASSED, plain `--strict` reference run failed | 1 | NO-RESULT for parity; a green determinism cell with no reference stdout |
| unaccounted | 0 | must be 0 |

The second reference class is a single named cell: `c-programs/dbi-pid-virtualization` (`ptrace-run-fail-exit124`; output hash is the sha256 of empty).

## Old vs new greens

`green` = `deterministic == 1`, i.e. the STRIPPED probe returned a determinism pass.

| backend | old green | old executed | old % | new green | new executed | new % | abs delta | pp delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dbi | 156 | 200 | 78.0% | 158 | 205 | 77.1% | +2 | -0.9 |
| e9patch | 179 | 200 | 89.5% | 181 | 205 | 88.3% | +2 | -1.2 |
| kvm | 130 | 200 | 65.0% | — | 0 | no-result | — | — |
| liteinst | 118 | 200 | 59.0% | 121 | 205 | 59.0% | +3 | +0.0 |
| ptrace | 179 | 200 | 89.5% | 183 | 205 | 89.3% | +4 | -0.2 |
| sabre | 164 | 200 | 82.0% | 152 | 205 | 74.1% | -12 | -7.9 |
| **TOTAL** | **926** | **1200** | **77.2%** | **795** | **1025** | **77.6%** | **-131** | **+0.4** |

## Same numerator, two denominators — the definition change alone

| backend | green | nominal 235 | effective 205 | pp moved by the definition change |
| --- | ---: | ---: | ---: | ---: |
| dbi | 158 | 67.2% | 77.1% | +9.8 |
| e9patch | 181 | 77.0% | 88.3% | +11.3 |
| liteinst | 121 | 51.5% | 59.0% | +7.5 |
| ptrace | 183 | 77.9% | 89.3% | +11.4 |
| sabre | 152 | 64.7% | 74.1% | +9.5 |
| **TOTAL** | **795** | **67.7%** | **77.6%** | **+9.9** |

Not one additional cell passed between those two columns; only the denominator moved.
