# Shared-memory executable object experiments

This directory develops Rust state and executable methods that trusted Linux
processes can map and invoke directly. Each iteration is independently
buildable so its assumptions and evidence remain reproducible.

## Iterations

- [`latest/`](latest/) points to the current `shmem-pod` library and executable
  image harness. Start there for the supported API and examples.
- [`v2/`](v2/) is the current publishable library workspace.
- [`v1/`](v1/) preserves the initial relocation-free code, fixed-layout state,
  and `LD_PRELOAD` process-tree experiment.

The current runtime also has an unpublished
[`LD_PRELOAD` integration demo](v2/demos/preload/) whose guest has no pod
dependency or guest-side bootstrap calls.

The iterations have separate Cargo workspaces and lockfiles. Run their complete
checks independently (`jq` is required for Cargo artifact discovery):

```console
$ ./v1/scripts/run-poc.sh
$ ./v2/scripts/run-poc.sh
$ ./v2/scripts/run-preload-demo.sh
```

Research results, design records, and blind API reviews live in
[`ai_docs/`](ai_docs/).
