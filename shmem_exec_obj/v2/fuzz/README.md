# Adversarial fuzz targets

This nested workspace is intentionally outside the release workspace and is
never published. Generate the deterministic seed corpus before a run:

```sh
cargo run --manifest-path fuzz/Cargo.toml --bin generate-corpus -- fuzz/corpus
```

Run one bounded target from the `v2` directory:

```sh
cargo fuzz run image_header fuzz/corpus/image_header -- \
  -max_total_time=60 -max_len=1048576 -timeout=5
```

The supported target names are `image_header`, `pod_artifact`,
`bootstrap_context`, `layout_descriptor`, and `offset_resolution`. Corpus,
artifacts, and target output are ignored; only the generator and target source
belong in version control.
