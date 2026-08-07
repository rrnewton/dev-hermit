# Env-block parity across Hermit backend arms

**Date:** 2026-08-06 · **Host:** devbig014 · **Task:**
`equalise-env-blocks-across-preload-and-ptrace-arms`

## Question

The patching/preload arms and the ptrace arm must hand the guest the **same
environment block**. If they do not, every env-derived guest observable
diverges for a reason that has nothing to do with backend semantics — guest
stdout differs for any program that reads its own environment, and every
`--detlog-stack` hash differs because the env strings live inside the hashed
`[stack]` VMA (see `detlog-stack-hashes-the-environment-block`). Such a
divergence reads as a backend finding but is an artifact of the measurement
setup.

Where do the arms' env blocks actually differ, and what causes each difference?

## Method

`compat-envelope/check-env-block-parity.rs` runs one fixed guest
(`compat-envelope/fixtures/env_block_probe.c`) under each arm with a pinned
base env and compares the guest-visible environment against the ptrace
reference.

The pinned env is load-bearing: with `--base-env host` the arms would differ by
whatever the two shells happened to carry, so the comparison would measure
nothing.

```
hermit run --backend <arm> --base-env minimal -e LC_ALL=C -e TZ=UTC -- <probe>
```

**Three channels, compared separately**, because a backend can equalise one and
not the others:

| channel | what it is |
|---|---|
| `environ` | the libc array `getenv()` and `environ` walks see |
| `procenv` | `/proc/self/environ` as an **entry list** |
| `rawblock` | the same kernel block compared **byte for byte** |

The third is the one that matters and the one a naive check omits. A backend
can hide an injected variable by zeroing its bytes and shifting it out of
`environ`; both entry lists then match perfectly while the block is still
longer by the length of the blanked entry.

## Results

| arm | envN | envB | procN | procB | rawB | verdict | Δ vs ptrace |
|---|---:|---:|---:|---:|---:|---|---:|
| `ptrace` | 7 | 183 | 7 | 183 | 183 | REFERENCE | 0 |
| `ptrace#control` | 7 | 183 | 7 | 183 | 183 | **IDENTICAL** | 0 |
| `sabre` | 7 | 183 | 7 | **261** | **261** | **DIVERGES** | **+78** |
| `dbi` | 11 | 2347 | 11 | 2347 | 2347 | **DIVERGES** | **+2164** |

### sabre — invisible to an `env`-based comparison

sabre's `environ` is byte-identical to ptrace's: the same 7 variables, 183
bytes. Its **kernel block is 78 bytes larger**, a single run of **79 NUL bytes
at offset 175** where ptrace has one NUL.

Cause, confirmed causally rather than by inspection — the hole tracks the
socket path length exactly:

| `TMPDIR` length | block bytes |
|---:|---:|
| 4 (`/tmp`) | 261 |
| 51 | 308 |
| 74 | *(run fails — path exceeds the `sockaddr_un` limit; separate issue)* |

+47 chars of `TMPDIR` → +47 block bytes. The blanked entry is
`REVERIE_SABRE_HERMIT_RPC_SOCKET=<tmpdir>/…/coordinator.sock`, set by
`hermit-cli/src/lib.rs:1039` and erased in the guest by
`reverie/experimental/reverie-sabre/src/paths.rs:63` (`take_private_env`).

`take_private_env` is doing exactly what it says: it removes the entry from
`environ` and zeroes the backing bytes so the socket path cannot leak through
`/proc/self/environ`. What it **cannot** do is shrink the block — `env_start`/
`env_end` are fixed at exec. Its own test
(`take_private_env_scrubs_proc`, `paths.rs:147`) asserts the secret *substring*
is absent. That is a content predicate; the parity-relevant fact is a
**length/shape** predicate, and nothing asserts it. So the divergence is not a
bug in the scrub — it is an uncovered consequence of it.

**The residual size is not even a constant**: it varies with `TMPDIR`, so no
fixed pad could absorb it.

### dbi — 4 extra variables, two different owners

| variable | bytes | owner |
|---|---:|---|
| `HERMIT_DBI_DETCONFIG` | ~2027 | **hermit** (`hermit-cli/src/lib.rs:1387`, `bin/hermit/backends.rs:446`) |
| `DYNAMORIO_EXE_PATH` | varies (guest path) | third-party `drrun` |
| `DYNAMORIO_CONFIGDIR` | 24 | third-party `drrun` |
| `DYNAMORIO_TAKEOVER_IN_INIT` | 28 | third-party `drrun` |

93% of the delta is Hermit serialising its whole `DetConfig` as JSON into the
*guest's* environment. `DYNAMORIO_EXE_PATH` embeds the guest's own path, so
this delta also varies per cell.

## Interpretation

1. **The prior conclusion that sabre is clean was an artifact of the
   instrument.** It was reached by running `/usr/bin/env` under each arm and
   diffing, which reads only the `environ` channel — precisely the channel
   `take_private_env` equalises. Byte-exact block comparison is required.
2. **`e9patch` needs no env work**: it is not a Detcore backend; the Detcore
   backend is ptrace in both of its arms, so no env delta exists.
3. **Padding the ptrace arm is the wrong direction.** It would make a program
   that is *not* running under DynamoRIO see `DYNAMORIO_*` variables, and for
   sabre the pad length is `TMPDIR`-dependent anyway. Contaminating the clean
   arm to make numbers match is the vacuous equalisation this work exists to
   prevent.
4. **The right fix is to keep run-specific, backend-specific data out of the
   guest's exec-time env block**, since it can be hidden after exec but never
   removed:
   - *sabre*: pass the coordinator socket to the runner as a CLI argument (or
     an inherited fd) instead of `command.env(SABRE_RPC_SOCKET_ENV, …)`. The
     runner process *is* the guest process, so anything in its env at exec is
     permanently in the guest's block.
   - *dbi*: pass the `DetConfig` via a file whose path is a DynamoRIO **client
     argument** (the mechanism already used for
     `-panic-on-unsupported-syscalls`). It cannot be passed inline: DR's
     `MAX_OPTIONS_STRING` is 2048 and the JSON is ~2027 bytes plus the flag.
   - *the 3 `DYNAMORIO_*` residuals* are third-party. DR does have an in-place
     `disable_env()` (`core/unix/os.c`), but it clobbers the name rather than
     removing the entry, and DR relies on these propagating to children for
     follow-children injection — so scrubbing them risks a child escaping
     instrumentation. Not a drive-by fix; declared as residuals instead.
5. **Do not spend the prefix-depth ratchet on cross-backend stack parity yet.**
   Earlier measurement on this task showed ptrace-vs-DBI stack hashes stay
   0/36 shared even after handing ptrace DBI's exact extra variables, so the
   env is a real cause of stack-hash sensitivity but not the cause of the
   cross-backend divergence. Equalising the env is necessary, not sufficient.

## Verification — both brackets

A check that only ever says DIVERGES proves nothing, and an allowlist can hide
a hole that was never checked. Five live runs:

| bracket | run | expected | observed | exit |
|---|---|---|---|---|
| positive | `--backends ptrace,ptrace#control` | IDENTICAL | IDENTICAL | 0 |
| negative (added var) | `--plant PLANTED_ENV_PARITY_PROBE=1` into the control arm | caught | caught, named | 1 |
| negative (changed value) | `--plant TZ=UTC-7` into the control arm | caught | `TZ=UTC -> TZ=UTC-7` | 1 |
| non-inert allowlist | `--backends ptrace,dbi --residuals none` | the 3 declared residuals fire | all 3 fired | 1 |
| canonical | `--backends ptrace,ptrace#control,sabre,dbi` | control identical; 2 production arms diverge | observed exactly; none unavailable | 1 |

The allowlist bracket matters: with the allowlist dropped, every tolerated
residual must show up. If it did not, the allowlist would be excusing something
the check was never able to see.

The plant is deliberately inert with respect to any authorization — it only
adds a guest environment variable, so it cannot itself cause the condition it
tests for.

Two fail-closed parser/availability brackets are unit-tested with
`rust-script --test compat-envelope/check-env-block-parity.rs`: malformed raw
hex is refused rather than converted to zero bytes, and one matching control
cannot turn a requested unavailable arm into exit 0.

## Reproduction

```
cd /home/newton/work/dev-hermit

# For the frozen table, build from the exact Hermit SHA in metadata.json in a
# registered clean checkout. This also stages the pinned SaBRe/DBI resources.
HERMIT_CHECKOUT=/path/to/registered/worktree/hermit
make -C "$HERMIT_CHECKOUT" build
HERMIT_BIN="$HERMIT_CHECKOUT/target/debug/hermit"
export HERMIT_SABRE_BINARY="$HERMIT_CHECKOUT/target/install_pkg/rsrcs/sabre"

# canonical run (exits 1 today: sabre and dbi genuinely diverge)
./compat-envelope/check-env-block-parity.rs \
    --hermit "$HERMIT_BIN" \
    --backends ptrace,ptrace#control,sabre,dbi --json /tmp/parity.json

# positive bracket
./compat-envelope/check-env-block-parity.rs --hermit "$HERMIT_BIN" \
    --backends ptrace,ptrace#control

# negative bracket
./compat-envelope/check-env-block-parity.rs --hermit "$HERMIT_BIN" \
    --backends ptrace,ptrace#control \
    --plant PLANTED_ENV_PARITY_PROBE=1 --plant-arm 'ptrace#control'

# allowlist is not inert
./compat-envelope/check-env-block-parity.rs --hermit "$HERMIT_BIN" \
    --backends ptrace,dbi --residuals none

# sabre causal test: the hole tracks the socket path length
# Keep the nested directory outside /tmp: Hermit's container isolates guest
# /tmp, so a nested host /tmp path makes the SaBRe RPC directory unavailable.
long_tmp=/home/newton/work/dev-hermit/scratch/xxxxxxxxxxxxxx
test "${#long_tmp}" -eq 51
mkdir -p "$long_tmp"
TMPDIR=/tmp ./compat-envelope/check-env-block-parity.rs \
    --hermit "$HERMIT_BIN" --backends ptrace,sabre
TMPDIR="$long_tmp" ./compat-envelope/check-env-block-parity.rs \
    --hermit "$HERMIT_BIN" --backends ptrace,sabre
```

The probe binary is built from tracked source into `ignored/envparity/` and is
not committed. It must not live under `/tmp` — hermit isolates the guest's
`/tmp`.

## Status

The **instrument** is delivered and bracket-verified. The **two equalisations**
it identifies (sabre socket path, dbi `DetConfig`) are product changes in
`hermit-cli` plus, for sabre, the reverie runner; they are specified above but
not implemented here. The check is what makes either of them provable when
done: today it reports `1 control identical, 2 production arms diverging` and
would flip the two production arms to identical on success — which is the
point, since neither fix can be believed on the strength of a smaller byte
count alone.
