# An engagement witness is required alongside every strace-litmus PASS

**Measurement only.** No perf work, no product change.

## The ambiguity this closes

The strace-attach litmus asks: can `strace` attach to hermit and the guest still
run? A PASS is supposed to mean *no ptracer is in the guest's path*.

But a PASS with **no engagement witness** is ambiguous between two opposite
readings:

- the backend ran the guest and genuinely needs no ptracer — the architecture claim; or
- **the backend path never engaged at all**, and something else ran the guest.

Both produce `rc=0` and both produce the guest's output. So the litmus alone
cannot distinguish them, and a witness-less PASS must never be recorded as an
architecture claim.

## Witness per backend, with strength graded

A *witness* is output that only an engaged backend path can produce. A *label* is
output that names the backend regardless of whether it did anything.

| backend | witness | strength | where |
|---|---|---|---|
| `dbi` | `rewritten=N`, require **N>0** | **strong** — counts work only the rewriting path performs | default stderr |
| `sabre` | `guest_rpc_observed=true` | **strong** — affirmative; a silent ptrace fallback cannot produce it | **`-l info` only** |
| `kvm` | `execution_us=N` (lifecycle phase timings) | **medium** — see below | **`-l info` only** |
| `e9patch` | `mapped_sites=N` | weak here — measured **0** on this guest | default stderr |
| `liteinst` | none found | **none** | — |
| `ptrace` | `metrics=none` | **label, not a witness** | `-l info` |

### Why `kvm`'s is medium and not strong

Established by construction, because the obvious empirical test turned out inert
(below). In `hermit-cli/src/lib.rs`, the `execution_us` line at 1328 is reachable
only after a `?`-propagating chain at 1279–1291:

```
reverie_kvm::KvmBackend::new_with_stdin(...)         .map_err(...)?
backend.set_root_pid(...)                            .map_err(...)?
backend.install_static_elf_with_context(...)         .map_err(...)?
backend.set_random_seed(...)                         .map_err(...)?
```

If KVM cannot initialize, the function returns `Err` and the line is never
emitted. There is **no ptrace fallback in this path** — the only `fallback` code
in the file (1022–1087) belongs to SaBRe, in a different function.

So `execution_us` does prove `KvmBackend` initialized and loaded the guest. What
it does **not** do is count any virtualized work, so it cannot distinguish "KVM
virtualized the guest" from "an initialized KVM backend ran the guest while
intercepting nothing". That is why it is medium: strictly better than a label,
strictly weaker than `rewritten=N`.

### An inert control, recorded so it is not repeated

I tried to negative-test the KVM witness by denying `/dev/kvm`:

```
systemd-run --user --scope -p DeviceAllow= -p DevicePolicy=strict ...
```

The run still succeeded and still printed `execution_us=298067`, which looked like
proof the witness was fake. **It was not proof — the denial never bound.** A direct
probe in the same scope shape shows `/dev/kvm` remained openable: cgroup-v2 device
control is eBPF-based and a `--user` scope cannot apply it. The control was inert,
so it establishes nothing in either direction, and the conclusion it appeared to
support is withdrawn.

## The corrected litmus table

Same run as the per-backend litmus, at `-l info` so witnesses are visible, with
every `rc` captured directly from the command and written to a file — never read
through a pipeline, because `$?` after `cmd | tr` is `tr`'s status.

| backend | strace rc | guest ran | witness | reading |
|---|---:|---|---|---|
| `ptrace` | 1 | no | — | **FAIL** — ptracer in path |
| `e9patch` | 1 | no | — | **FAIL** — ptracer in path |
| `sabre` | 1 | no | — | **FAIL** — ptracer in path |
| `liteinst` | 1 | no | — | **FAIL** — ptracer in path |
| `dbi` | 0 | yes | `rewritten=38` | **PASS + strong witness** |
| `kvm` | 0 | yes | `execution_us=382570` | **PASS + medium witness** |

No backend in this run is a witness-less PASS. That is a fact about this run, not
a property of the harness — the category exists and must stay in the table, or the
next backend that lands one will be read as an architecture claim.

## The rule

1. Record the witness **alongside** every litmus result, not instead of it.
2. A PASS with no witness is reported as **`PASS-NO-WITNESS — AMBIGUOUS`**, never
   as "no ptracer in the path".
3. Grade the witness. A label (`backend=ptrace`, `launching guest through …`) is
   not a witness; a count of backend-specific work is the strongest form.
4. Capture `rc` directly and redirect to files. `$(cmd | tr …)` yields `tr`'s
   status and silently turns every failure into a pass.

## Limitations

One host, one guest (`/bin/echo`). `liteinst` and `e9patch` fail the litmus, so
their witness columns were not exercised on a passing run — `liteinst`'s "none
found" is from source and log inspection, not from a passing run that lacked one.
`e9patch`'s `mapped_sites=0` is specific to this guest.
