# Rescued evidence — backend-parity-c cell audit

Copied verbatim from `/tmp/bpc2/` on 2026-08-06, because `/tmp` on this host is reaped and
had already destroyed working binaries twice the same day. Without this copy the whole
83-cell measurement would have to be re-run.

**Integrity of the rescue:** the sorted set of sha256 sums over all 83 `junit.xml` files was
compared before and after the copy and matched exactly (`e635caab4d763e3529fc24b03d65dd5a`).

**One deliberate modification:** 33 compiled fixture binaries (`runs/*/fixtures/program`, ELF,
~24K each) were REMOVED before committing. Repository policy forbids committing binaries, and
they are reproducible by compiling the corresponding `tests/backend-parity/fixtures/*.c`.
Every text artifact is preserved: 83 `junit.xml`, 116 `.stdout`, 116 `.stderr`, `r.jsonl`,
`ids.txt`, and the per-run git configs.
