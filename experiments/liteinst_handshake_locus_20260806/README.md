# LiteInst preload handshake: the container-path hypothesis is refuted, and the locus moves

**Task:** `close-top-gap-cells-toward-100` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Stating the outcome first

**No cell went green. I closed zero red program×backend cells.** What I did was settle the one open
question the prior note on this task left dangling — and the answer is a refutation, which relocates
the root cause.

## Context: why liteinst is the only thread available

The prior note's ownership check (which I re-read and did not repeat) eliminated the top-ranked gaps:
`dbi` owned twice, `e9patch` owned, `kvm` owned; `sabre` unbuildable here (no `cmake` on this host).
**LiteInst backend availability was the one unowned thread**, and it was narrowed to a specific
function with a *named but unverified* hypothesis:

> "**LEADING HYPOTHESIS, NOT CONFIRMED:** hermit runs the guest in a container with a scoped filesystem
> view, so the DSO's path inside the guest's `/proc/PID/maps` may not equal the host-canonicalized
> `config.preload`, making `preload_code()` false and returning `None`. I did NOT verify this — the
> guest dies too fast to read its maps."

The note also recorded a correction it had already made mid-investigation (the standalone `LD_PRELOAD`
SIGTRAP probe was invalid, since `int3` is the *designed* handshake). I kept that correction in view.

## The validator, confirmed as described

`reverie/reverie-ptrace/src/task.rs:2106-2160`. The path check is exactly as the note reported —
**exact equality**, against the guest's own maps:

```rust
let maps = guest_maps(task.pid())?;
let preload_code = |address| {
    maps.iter().any(|mapping| {
        mapping.executable
            && mapping.path.as_ref() == Some(&config.preload)
            && mapping.contains(address)
    })
};
```

So the hypothesis was well-formed: if the guest's rendered path differs from `config.preload`, every
one of the seven handshake RIPs fails `preload_code` and the validator returns `None`, which is
precisely the observed `phase Waiting` failure.

## Why it is refuted — settled by construction, not by catching the dying guest

Three facts, each read from source:

1. **Hermit does not chroot on an ordinary run.** *Every* `chroot` site in
   `hermit-cli/src/bin/hermit/run.rs` is gated on `--image`:
   * `:232` "Incompatible with `--image`, which chroots the guest into a materialized…"
   * `:279` "…and the guest is `chroot`ed into a…"
   * `:1947` "`--image` chroots the guest into a materialized OCI rootfs…"
   * `:2882` "with a chroot into the pinned image root"

   The chroot-bearing container builder in `container.rs:168-196` (`Mount::bind(rootfs, rootfs)`,
   remount-RO, `container.chroot(rootfs)`) is that `--image` path. The ordinary path calls
   `identity_hardening_mounts()` from `run.rs:2821` and builds its own container **without** it.

2. **The ordinary path remaps only `/tmp`.** `run.rs::mounts()` replaces `/tmp` with a tmpfs and
   rewrites `--mount`/bind targets under it. Its own comment says the rest out loud: *"files outside
   `/tmp` are already visible unless another mount hides them."*

3. **The DSO is staged outside `/tmp`.** `scripts/stage-liteinst-runtime.sh` stages into
   `realpath -m -- "$3-${reverie_pin:0:8}"` — i.e. under `target/`, which is not the tmpfs target and
   not otherwise overmounted.

**Therefore the guest's view of the staged DSO is not remapped, and `mapping.path == config.preload`
is not where the validator fails.** The hypothesis is refuted for the default (non-`--image`) path,
which is the path the failing run uses.

This is the cheap resolution the prior note wanted: it did not need the guest's maps, only the mount
plan.

## Where the locus moves

`validate_liteinst_handshake` returns `None` on any of these, and the path check is now excluded:

| remaining condition | note |
| --- | --- |
| `frame.version != 4` | a version skew between the staged DSO and reverie's expectation |
| `frame.helper_stack_top < 8` | |
| `frame.helper_stack_top & 0xf != 0` | 16-byte alignment |
| `trap_rip != frame.begin_rip` (or `ready_rip` when `ready`) | the trap fired but at an unexpected RIP |
| `frame_readable` / `helper_stack_map` / `install_result_writable` | the trailing mapping checks |

The prior note already flagged `frame.version != 4` and `helper_stack_top` as "alternatives not
excluded". They are now the *primary* candidates, and **`frame.version` is the cheapest to test**: the
staged DSO and reverie were built from different trees in that session (hermit was rebuilt; the
runtime came from the locked `liteinst-runtime-build`), so a struct-version skew is the hypothesis
that best fits "the constructor runs, the int3 fires, the validator rejects".

**Next step, concretely:** read the `LiteinstHandshakeFrame` version constant in the staged DSO's
source tree and compare it to reverie's `version != 4` check. That is a static comparison — it needs no
running guest, which is what made the previous attempt stall.

## Limits

* **No cell closed, no code changed, nothing committed to a product repo.**
* The refutation covers the **default run path**. If liteinst is ever exercised under `--image`, the
  chroot path *does* remap and the original hypothesis would apply there.
* I did **not** confirm the new leading candidate (`frame.version`). It is a hypothesis, ranked by fit,
  not a finding.
* I did not attempt per-cell work; the prior note records that the gap map treats it as meaningless
  until the ranked blockers clear.
