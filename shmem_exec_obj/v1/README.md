# Executable shared-memory object: first experiment

This preserved experiment tests the smallest viable Linux design:

1. Compile `no_std` Rust methods as position-independent machine code.
2. Reject external and runtime relocations.
3. Map code read/execute and shared state read/write, never read/write/execute.
4. Load a small `LD_PRELOAD` shim into an otherwise unmodified guest.
5. Have every process in a fork/exec tree call the mapped methods concurrently.

The shared state contains coarse and sharded spin locks, atomic counters, and a
fixed connection table. The loader verifies the image header and layout before
dispatching through an explicit C ABI method table.

Run the complete compiler, negative-fixture, mapping, process-tree, and counter
checks with (`jq` is required):

```console
$ ./scripts/run-poc.sh
```

This iteration deliberately uses a strict fixed layout. The current library in
[`../v2/`](../v2/) adds structural traits, layout fingerprints, relative
offsets, fixed-address allocation, SNZI, and a sealed executable-image runtime.
