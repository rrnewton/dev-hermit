# Pending: attribution wiring for `ci-hub/stress/` (uncommitted upstream)

At the time attribution was shipped, `ci-hub/stress/` (the nightly-stress-harness)
was **untracked** — another agent's in-flight, not-yet-committed work. Per the
"do not absorb another agent's uncommitted changes" invariant, the attribution
wiring for two files in that directory was **not** committed with the attribution
tool. It is recorded here so it lands when `ci-hub/stress/` itself is committed,
and survives any reset of those untracked files.

The nightly's **real** hot-loop sink is
`experiments/multisect_detcore_misc_20260803/matched.sh` (tracked), which **was**
wired and committed. `stress-burst` is the generic/multisect primitive and
`nightly.sh` is the driver-side surfacing; both are additive and env-gated
(`STRESS_CAPTURE_DIR`), so the default behavior is byte-identical when unset.

Both edits were already applied to the working-tree copies; re-apply only if a
reset removed them (verify with `grep -n STRESS_CAPTURE_DIR ci-hub/stress/*`).

---

## `ci-hub/stress/stress-burst`

**Hunk 1** — after `SHORT="$(git -C "$CO" rev-parse --short HEAD …)"`, before the
"Locate the newest test binary" comment, insert:

```bash
# Optional evidence capture (attribution). When STRESS_CAPTURE_DIR is set, each
# FAILING burst instance preserves a bundle (stdout/stderr/exit/host-conditions)
# that `attribution.py` can turn from a bare hang-RATE into a CAUSE. Off by
# default => the burst loop below stays byte-for-byte the old >/dev/null idiom.
# Pure bash (capture-run.sh) so the hot loop never forks a per-instance Python.
CAPTURE_DIR="${STRESS_CAPTURE_DIR:-}"
CAPTURE_SH="$(cd "$SELF/.." && pwd)/attribution/capture-run.sh"
if [ -n "$CAPTURE_DIR" ] && [ ! -x "$CAPTURE_SH" ]; then
  echo "stress-burst: STRESS_CAPTURE_DIR set but $CAPTURE_SH missing/x; capture OFF" >&2
  CAPTURE_DIR=""
fi
[ -n "$CAPTURE_DIR" ] && { mkdir -p "$CAPTURE_DIR"; export STRESS_CAPTURE_SHA="$SHORT"; }
```

**Hunk 2** — replace the burst loop:

```bash
for _ in $(seq 1 "$WIDTH"); do
  ( timeout "$TIMEOUT" "$BIN" "$TEST_PATH" --exact --test-threads=1 >/dev/null 2>&1
    echo "$?" >>"$LOG/burst.txt" ) &
done
```

with:

```bash
for i in $(seq 1 "$WIDTH"); do
  if [ -n "$CAPTURE_DIR" ]; then
    # capture-run.sh runs the timeout itself, prints the exit code, and on
    # failure preserves a bundle under CAPTURE_DIR. Label pins the SHA so a
    # multi-SHA capture dir stays attributable.
    ( ec="$("$CAPTURE_SH" "$CAPTURE_DIR" "${SHORT}-inst${i}" "$TIMEOUT" -- \
             "$BIN" "$TEST_PATH" --exact --test-threads=1)"
      echo "$ec" >>"$LOG/burst.txt" ) &
  else
    ( timeout "$TIMEOUT" "$BIN" "$TEST_PATH" --exact --test-threads=1 >/dev/null 2>&1
      echo "$?" >>"$LOG/burst.txt" ) &
  fi
done
```

---

## `ci-hub/stress/nightly.sh`

**Hunk 1** — after the `ts()` / `log()` helpers, insert:

```bash
# Evidence capture for ATTRIBUTION. Default ON for the nightly: the whole point of
# this run is to catch a flake, and a flake we cannot attribute is half-useful. We
# set STRESS_CAPTURE_DIR so the shared burst primitive (which flows it down to
# matched.sh) preserves a bundle per FAILING instance; then on any non-CLEAN
# verdict we fold `attribution.py report` into the alarm so the P0 states a CAUSE
# (INFRASTRUCTURE / HERMIT_NONDETERMINISM / ENVIRONMENT / …), not just a rate.
CAPTURE="${STRESS_CAPTURE:-1}"
CAPBASE="$ROOT/ignored/ci-hub/stress-capture"
ATTR_PY="$ROOT/ci-hub/attribution/attribution.py"
```

**Hunk 2** — insert the `attribute_capture()` helper immediately before
`raise_alarm()`, and extend `raise_alarm` to fold attribution into the detail +
JSON marker (adds a 4th `$4=capture_dir` arg and a new `"attribution"` field). See
the working-tree copy for the exact block; its shape:

```bash
attribute_capture() {          # $1=capture_dir $2=marker_path
  # returns one-line ATTRIBUTION SUMMARY; writes <marker>.attribution.txt sidecar
  ...
  report="$(python3 "$ATTR_PY" report "$capdir" 2>/dev/null)" || return 0
  printf '%s\n' "$report" > "${marker%.json}.attribution.txt"
  sed -n 's/^ATTRIBUTION SUMMARY: //p' <<<"$report" | tail -1
}
raise_alarm() {                # $1=wl $2=verdict $3=detail $4=capture_dir
  local attr; attr="$(attribute_capture "${4:-}" "$marker")"
  local detail="$3"; [ -n "$attr" ] && detail="$3 | ATTRIBUTION: $attr"
  # ...marker JSON gains "attribution":"$attr"; escalation uses $detail...
}
```

**Hunk 3** — in the per-workload loop, before the `if [ -z STRESS_BURST_CMD ]`,
set the per-workload capture dir:

```bash
  wl_cap=""
  if [ "$CAPTURE" != "0" ]; then
    wl_cap="$CAPBASE/$(printf '%s' "$wl" | tr ':/' '__')-${SHA:0:12}-$(TZ=UTC date +%Y%m%dT%H%M%SZ)"
    export STRESS_CAPTURE_DIR="$wl_cap"
  else
    unset STRESS_CAPTURE_DIR
  fi
```

**Hunk 4** — pass `$wl_cap` to the alarm:

```bash
    2) raise_alarm "$wl" "${verdict:-RED}" "${summary#VERDICT=* }" "$wl_cap" ;;
```
