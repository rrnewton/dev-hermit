# Hermit Week-1 Progress Report

Date: 2026-07-28

## Executive summary

The first week converted Hermit from a ptrace-only development baseline into a
multi-backend determinism platform with six CLI-selectable backend values and
four backends in the first common scorecard: ptrace, KVM, DBI, and SaBRe. The
current landed scorecard records 14/15 successful non-ptrace backend-local
`--strict --verify` example cells and 5/15 byte-identical guest-output matches
against ptrace, up from 0/15 in the previous available-backend snapshot.

Across the four version-controlled repositories, the trailing seven-day
window contains 1,254 commits and Git numstat totals of 430,754 lines added and
52,510 removed. These are repository activity figures, not unique authored
source lines: generated inventories, vendored or generated text, submodule
pointer updates, and changes later revised in the same window are included.

The main frontier additions are:

- public backend selection and working ptrace, KVM, DBI, and SaBRe scorecard
  paths, with LiteInst and e9patch also exposed as CLI values;
- restored fail-closed `--strict` execution combined with two-run `--verify`;
- a source-revisioned strict L2 QEMU/TCG Linux boot result;
- CI overhaul v2, with centralized TOML manifests, structured parsing,
  inventory auditing, explicit backend/mode partitions, and shared DAGs;
- a cumulative B0-B4+ backend maturity model and a first measured parity
  baseline; and
- a new standalone LiteInst2 implementation plus a Reverie LiteInst tool host.

This report audits landed history and durable evidence. It does not rerun every
runtime claim at the repository tips listed below.

## Measurement method

Measurement time: `2026-07-28T16:26:52Z`. The exact trailing window begins at
`2026-07-21T16:26:52Z`.

Each repository was fetched and measured from `origin/main`:

```bash
with-proxy git fetch origin main
git rev-list --count --since='7 days ago' origin/main
git log --since='7 days ago' --numstat --format=tformat: origin/main
```

The additions and removals below are sums of numeric numstat columns. Binary
entries (`-`/`-`) are not assigned synthetic line counts. Commit counts include
merge commits whose commit date falls within the window. This is intentionally
an activity measure rather than a net tree diff.

## Repository activity

| Repository or scope | Measured `origin/main` | Commits | Added | Removed | Net | Binary entries |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `rrnewton/dev-hermit` | `059110437a719b0df592e0f400146c2cc59cd695` | 310 | 193,452 | 10,317 | +183,135 | 0 |
| `rrnewton/hermit` | `3df2b49fe8b82c499c0df68162b2fdbc587b76a1` | 710 | 158,602 | 36,558 | +122,044 | 2 |
| `rrnewton/reverie` | `7466b84f0df90e592b1eb02cc6bec3e88435b67d` | 223 | 71,457 | 5,397 | +66,060 | 0 |
| `rrnewton/liteinst2` | `b21b248294e6cbed1dd4a7ff01e7264c06741882` | 11 | 7,243 | 238 | +7,005 | 0 |
| **Four-repository total** | - | **1,254** | **430,754** | **52,510** | **+378,244** | **2** |

`reverie/reverie-liteinst` is a crate inside `rrnewton/reverie`, not a fifth
Git repository. As a path-scoped view it accounts for 12 of Reverie's commits,
5,978 additions, and 714 removals in the same window. Those figures are already
included in the Reverie row and are not added to the total. The separate
`liteinst2/` parent submodule is the standalone `rrnewton/liteinst2` repository.

## First bot-aided commit provenance

Squash merges commonly retain the human owner's Git identity, so author name
alone cannot identify agent assistance. This table uses the earliest explicit
repository-local evidence: an agent role tag, a commit body naming Codex or
Claude, or the addition of agent-specific contribution guides. Earlier
untagged commits may also have been agent-assisted.

| Repository or scope | First explicit bot-aided commit | Date | Evidence |
| --- | --- | --- | --- |
| `dev-hermit` | `7b1a5ea7f5e91405be01f7fca8f747c9dda6baa1` | 2026-07-21 14:38:13 -0700 | `Add parent workspace guidance for Claude` |
| `hermit` | `d814dbd037343f75cfd1569c49db4476f4e45e50` | 2026-07-20 14:55:23 -0700 | Commit body explicitly adds guidance for Codex and Claude contributors. |
| `reverie` | `113020109550c295e8cbc262f69d6daf69fa7fb1` | 2026-07-21 15:03:42 -0700 | Adds `AGENTS.md` and `CLAUDE.md` with the fork CI workflow. |
| `reverie/reverie-liteinst` crate | `190626bc1fef01a3c849c8123e9926ea9b83c77c` | 2026-07-24 12:15:41 -0400 | Subject begins `[impl agent, gpt-5.6-sol]`; first commit touching the crate. |
| `liteinst2` repository | `d525a01c774754170b947f7319c948d9d8d085a8` | 2026-07-24 23:15:09 -0400 | Subject begins `[impl agent, gpt-5.6-sol]`. The repository itself began one day earlier at `41ce6e5b22b408c9ec7ec0b424393894502002a8`. |

The parent repository itself began at
`145c312b1da16191ed6916a0a2a91f9c09533670` on 2026-07-21 08:26:47 -0700,
which establishes the workspace's week-one start but does not itself contain
an explicit bot marker.

## Old Hermit versus current frontier

The old-Hermit baseline is the parent of the first explicitly bot-aided Hermit
commit: `2d67d22eb782990c916accbde1ea33d60d2ae2a3` (2026-07-17). The frontier
column describes landed evidence available by the measured tips above.

| Capability | Old-Hermit baseline | Current landed frontier | Evidence boundary |
| --- | --- | --- | --- |
| Execution backends | One implicit ptrace path; no public `Backend` enum or `--backend` selector. | Six CLI values parse: `ptrace`, `dbi`, `liteinst`, `sabre`, `kvm`, and `e9patch`. The common maturity scorecard currently grades ptrace, KVM, DBI, and SaBRe. | `hermit-cli/src/lib.rs` and `hermit-cli/src/bin/hermit/run.rs` at `3df2b49f`; scorecard parent commit `4d6cadba`. |
| Strict determinism | `--verify` existed, but the `--strict` clap field was commented out. | `--strict` is an explicit fail-closed mode and composes with `--verify`; unsupported syscalls fail instead of silently passing through. | Baseline `run.rs`; strict restoration and fail-closed behavior culminate in Hermit `bf1cab333bdb50aeeb952d7df9e7d586687153b0`. |
| Cross-backend local determinism | No common backend matrix because alternate backends were not selectable. | Non-ptrace backends pass 14/15 example `--strict --verify` cells: KVM 5/5, DBI 4/5, SaBRe 5/5. | `2026-07-28-examples-cross-backend-scorecard.md`, parent `4d6cadba`. |
| Cross-backend output parity | No measured alternate-backend denominator; effectively 0/15 available-backend matches. | 5/15 observable guest-output cells match ptrace: KVM 2/5, DBI 2/5, SaBRe 1/5. This is B2.1 diagnostic evidence, not a B3 full-corpus claim. | Same scorecard and maturity model. |
| Linux under QEMU/TCG | No source-revisioned Linux boot harness or QEMU gate in the baseline tree. | A strict sequentialized ptrace/QEMU-TCG boot reaches the initramfs oracle and passes two-run L2 verification. The recorded run compared 516,137 messages per verifier run with no substantive differences. | Hermit `8827c8302e52a75ba5621743a9b6703d59d30a2f`, `docs/QEMU_BOOT.md`. |
| KVM backend | No Hermit KVM ELF execution path. | Public KVM selection, a Reverie KVM guest executor, Detcore integration, Linux-guest support, syscall/process/filesystem/socket expansion, and real `/dev/kvm` tests. | Hermit `9f4447051d0fe39b5c174129c7bf9f2ff861f28b` and Reverie history through `7466b84f`. |
| DBI and in-process instrumentation | No public DBI or LiteInst Detcore execution path. | DynamoRIO DBI, LiteInst, and e9patch selectors are landed; Reverie supplies shared tool hosting and lifecycle/RPC support. | Hermit DBI/LiteInst history through `3df2b49f`; Reverie through `7466b84f`; LiteInst2 through `b21b2482`. |
| SaBRe | No public SaBRe execution selector. | SaBRe is selectable, drives Detcore through remote tool transport, and passes 5/5 backend-local example verification in the frozen scorecard. | Hermit/Reverie tips plus parent scorecard `4d6cadba`. |
| CI organization | No root `validate.sh`, centralized manifest schema, or shared portable/privileged test DAG. | CI overhaul v2 makes 13 TOML manifests load-bearing through a structured Rust parser and shared harness/DAG. Five calibrated buckets are blocking; 180 additional C guests are centrally discoverable with explicit mode/backend exclusions. | Hermit `419171a449ad12301564ccee2bca8295b9d5fbe1` through `2f2bf2220907d5230217679d0b5c0b1b637a0b33`. |
| Backend maturity claims | No cumulative cross-backend evidence standard. | B0-B4+ model separates build, interception, shared tools, Detcore entry, examples parity, complete corpus, and leading workloads. Current evidence conservatively places ptrace at B2.1 and KVM/DBI/SaBRe at B2 base. | Parent `26eb2aff6fe3cd240bf2446f4d9b97545833b56b`. |
| LiteInst implementation | No `liteinst2` repository and no Reverie LiteInst crate. | LiteInst2 implements cache-line scanning, atomic live patching, relocation-aware trampolines, probe lifecycle, and stress validation; Reverie hosts shared tools through the instrumentation layer. | LiteInst2 `41ce6e5b`..`b21b2482`; Reverie LiteInst path begins at `190626bc`. |

## Interpretation and remaining gaps

The week produced substantial breadth, but the evidence model prevents that
breadth from being mislabeled as parity. The four-backend examples scorecard
shows that backend-local determinism is close to complete at 14/15 while guest
semantics remain backend-dependent at 5/15 parity. DBI's `race.sh` scheduling,
logical time, random streams, and concurrent output policy are the immediate
B2.1 gaps.

Likewise, the strict QEMU/TCG Linux result is a strong named-workload L2 result,
not proof that every backend or every Linux workload has reached B4+. The CI
overhaul now provides the machinery to expand denominators without silently
turning undiscovered or unsupported cases green. Current maturity claims should
continue to be tied to exact Hermit/Reverie revisions and frozen manifest
counts.

At the measurement time, TaskGraph contained 3,018 closed tasks. This is
coordination throughput, not product correctness evidence; the repository
commits, scorecards, manifests, and exact runtime reports above are the durable
technical evidence.
