[impl agent, claude-opus-5]

Implements Part-B **Stack 1.3** (`dbi_log_file_is`). Unblocks 1.4 and cross-backend DETLOG comparison generally.

## Summary

The DBI backend ignored `--log-file`. Its in-guest DynamoRIO client had no sink
but `reverie_dbi::RuntimeEmitter`, which lands on stderr — so a run that asked
for a logfile got **no logfile and 267 DETLOG records on stderr**, while ptrace
wrote a 60744-byte logfile and left stderr empty. A cross-backend log comparison
therefore had to special-case DBI (read stderr) instead of reading the same
artifact for every backend, which is the remaining reason a cross-backend diff
is not push-button.

Measured before, `hermit --log=info --log-file=<path> run --backend <b> -- /bin/echo p`:

| backend | logfile | logfile DETLOG | stderr | stderr DETLOG |
| --- | ---: | ---: | ---: | ---: |
| ptrace | 60744 B | 269 | 0 B | 0 |
| **dbi** | **NONE** | **0** | **50137 B** | **267** |

After:

| backend | logfile | logfile DETLOG | stderr | stderr DETLOG |
| --- | ---: | ---: | ---: | ---: |
| ptrace | 60744 B | 269 | 0 B | 0 |
| **dbi** | **49746 B** | **267** | **391 B (2 lines)** | **0** |

## Approach — and why it stayed inside Hermit

The task suggested either a new reverie-side emitter targeting an inherited fd,
or Hermit writing to an fd it supplies, and noted the first would make this
cross-repo (reverie change **plus** a parent pin bump). The client is Hermit's
own code, so it can simply open the file and bypass the emitter — no Reverie
change, no pin bump.

`--log-file` already carries the env name `HERMIT_LOG_FILE`, and `--log` is
already forwarded to the guest as `HERMIT_LOG`, so the path rides that same
channel and the two logging controls reach the in-guest runtime the same way
(the guest inherits it from `drrun`, exactly as `HERMIT_DBI_DETCONFIG` does).

- Sink opened **once per client process**, in **append** mode, so sibling guest
  processes in one run do not truncate each other's records.
- **Fails soft:** variable unset, empty, or file unopenable → fall back to the
  emitter. A bad path degrades to the previous stderr behaviour rather than
  losing the log.
- Lifecycle markers follow the records into the file. They fire before tracing
  is initialized, but the sink does not depend on tracing, so even the first
  marker lands there and a DBI run leaves **one** artifact.

## Determinism

This changes *where* records are written, not what they contain or when they are
produced. No guest-visible behaviour, no scheduling, no syscall result, and no
Detcore state is touched: `DbiSubscriber::event` formats exactly the same line
and chooses a different file descriptor for it. The record count is unchanged
and reproducible — 267 DETLOG in the logfile on two consecutive runs of the same
guest — and the DETLOG content itself is produced by the same Detcore code paths
as before.

The sink is behind a `OnceLock`, so the open happens at most once per process
and the choice of destination cannot vary mid-run. Writes are serialised by a
`Mutex`, so interleaving between the client's threads is well-defined rather
than depending on write atomicity.

Worth stating explicitly: this makes DBI logs *comparable* to ptrace logs; it
does **not** by itself make them *equal*. Record counts remain 269 (ptrace) vs
267 (DBI) on the same guest, and closing that gap is separate work. What this
removes is the channel asymmetry that stopped the comparison being mechanical.

## Linux Semantics

No guest-observable change. `--log-file` is a Hermit diagnostic control; this
makes the DBI backend honour it as every other backend already does. When the
flag is absent, behaviour is byte-for-byte what it was (verified: 267 DETLOG on
stderr, as before).

## Validation

**Head:** `ece949c5ba8b83a9d2ab4e453c7b5438caf485bc`
**Base:** `origin/main` `4c70658e785834737cbe1524f77330c781a6f5ea` (0 behind, 1 ahead)
**Backends:** ptrace and DBI (`--features third-party-backends`) · **Relaxations:** none

| Check | Result |
| --- | --- |
| DBI produces a logfile | 49746 B, **267 DETLOG** (was NONE / 0) |
| **DBI stderr DETLOG** | **267 → 0** — proves the routing *moved*, not duplicated |
| Comparable record count | ptrace 269 vs DBI 267 on the same guest |
| ptrace unaffected | 60744 B logfile, stderr 0 B — unchanged |
| Reproducible | 2 consecutive DBI runs: 267 DETLOG both |
| Fallback preserved | no `--log-file` → 267 DETLOG on stderr, as before |
| Residual DBI stderr | 2 lines: Hermit's CLI banner + the reverie-dbi runtime summary; neither is a DETLOG record |
| Builds both feature sets | `--features third-party-backends` and default |
| `cargo test -p hermit-detcore --lib` | 388 passed, 0 failed |
| `cargo fmt --all -- --check`, `cargo clippy` (both feature sets) | clean |

**Premise correction, recorded for the task.** The prior investigation note left
the ptrace half *inconclusive* — it measured ptrace as producing no logfile and
suspected its own `/tmp` path was the confound. That suspicion was right: with a
guest-visible path (a home directory), ptrace writes a proper 60744-byte logfile
with empty stderr on a post-framing-fix build. The asymmetry was real and is
what this PR fixes; the ptrace row in that note should not be read as a ptrace
defect.

**Not claimed.** SaBRe and LiteInst not exercised. KVM untestable on this box.
This does not close DETLOG *parity* between ptrace and DBI — only the channel
asymmetry.

## Blocker

**No validate receipt.** `ci-hub validate-run` refuses at admission:
`preflight_validate.py` shells out to `with-proxy git fetch`, which is 403 from
an agent shell, and the only working egress (`herdr-run`) refuses `ci-hub`
(allowlist `cargo, gh, git`). This gates every stack in the serial landing plan.
Admission predicate computed locally: moving-base PASS, fixed-floor PASS.
