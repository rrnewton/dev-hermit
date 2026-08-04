# Acceptance probe: does reverie PR #355 restore known-good detcore_misc?

Date: 2026-08-03. Host: 316 cores, ambient load ~330 (1-min).
Probe: `matched.sh 32 20 24` — 24 waves × 32 concurrent single-shot instances of
each label, co-scheduled per wave so all share instantaneous host load. `head`
(reverie d973a85b, NO #355) is the validity calibrator: every wave was VALID
(head FLAKY 5–13/32, all 24 waves). Trinary PASS/FAIL/FLAKY; FLAKY=RED.

## Labels
- `head`         = hermit head binary, reverie d973a85b (no #355). Known-bad.
- `good5e190f7d` = hermit 5e190f7d, reverie pin 22791b2f. Multisect's LAST-CLEAN
                   (known-good) commit — the differential baseline.
- `pr355`        = hermit @ current worktree with reverie #355 (820b2b64) applied
                   via local [patch]; safeptrace at #355 baseline (no drive-to-exit).

## Result (hangs / 768 samples over 24 valid waves)
| label        | hangs/768 | rate   | verdict            |
|--------------|-----------|--------|--------------------|
| head         | 221/768   | 28.8%  | FLAKY (regression) |
| good5e190f7d | 5/768     | 0.65%  | CLEAN (noise floor)|
| pr355        | 4/768     | 0.52%  | CLEAN              |

## Conclusion
Differentially, against the multisect's own known-good SHA: **#355 restores
detcore_misc to known-good behavior.** pr355 (0.52%) is statistically identical
to known-good (0.65%) and both sit at the pre-existing noise floor the multisect
already classified CLEAN (e.g. d495b602 = 2/320 = 0.6%). #355 collapses the
fb2cf7e0 regression from 28.8% to the noise floor — it addresses the actual
regression, not a sibling symptom.

## Note on the residual ~0.5%
A separate, RARE true wedge ("Face B": detcore inject-`wait4` poll loop, distinct
from the getevent-ESRCH "Face A" #355 fixes) was live-confirmed (one instance
polling 214s) on an experimental reap `drive_to_exit` variant. It occurs at/below
the known-good noise floor and is NOT the fb2cf7e0 regression. The reap
`drive_to_exit` follow-up does NOT lower the rate below #355/known-good and is
therefore DROPPED. Any Face-B investigation is separate, pre-existing, and
non-blocking for main-green.

## Acceptance test for the pin bump
After #355 lands and hermit's reverie pin bumps past 820b2b64, re-run this exact
probe on the pinned main binary co-scheduled with `head`; require the pinned
binary at the known-good noise floor (≤ ~1/768) across valid (head-FLAKY) waves.
Raw waves: ignored/results/accept-pr355.log
