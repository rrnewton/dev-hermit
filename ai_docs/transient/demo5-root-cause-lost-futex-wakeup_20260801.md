# demo5 "wedge" RETRACTED: an under-budgeted-timeout artifact, not a wedge

Date: 2026-08-01. Author: impl agent, opus-4.8 (task `demo5-fix-scheduler-fairness-impl`).
Status: **RETRACTION.** Both prior conclusions in this file — the "lost futex
wakeup" working hypothesis and its "mutex/poller livelock" correction — are
**WITHDRAWN**. Under the shipped rcb-armed config on a quiet host, demo5 does
**not** wedge: it boots reliably in ~242 s. Every "0/3 / 0/6 byte-identical
wedge" datum below came from wall-clock timeouts (90–200 s) that were shorter
than demo5's real boot time, so a slow crawl was misread as a permanent wedge.

## What is actually true (validated 2026-08-01)

demo5, canonical shipped config (`hermit run --strict --target-timeslice 100000
--max-timeslice 2000000000`, RCB/PMU preemption ARMED — exactly what
`demos/05-qemu-boot.py` and `boot_sweep.py --rcb on` use), out-of-container
enforcer, quiet host (~17 % load, 316 cores), **real 600 s demo budget**:

| run | wall (s) | first-serial (s) | boot to RTC shell? |
| --- | --- | --- | --- |
| 500 s single | 242.9 | 30.0 | yes |
| 5-rep #0 | 238.3 | 29.8 | yes |
| 5-rep #1 | 242.8 | 29.3 | yes |
| 5-rep #2 | 247.6 | 30.0 | yes |
| 5-rep #3 | 242.8 | 29.5 | yes |
| 5-rep #4 | 242.8 | 29.8 | yes |

**6/6 PASS**, walls 238.3–247.6 s, median 242.8 s, tight distribution. The
boot reaches the `2022-01-01T` RTC shell marker and hermit exits 0 every time.

The serial transcript pauses for ~90 s at `hpet0: 3 comparators` (frozen at
exactly 17869 bytes mid-crawl) and then resumes and boots. That pause — the
exact state earlier runs captured and labelled a "17869-byte byte-identical
wedge" — is a **normal slow phase of the TCG-emulated boot**, not a terminal
state.

## The measurement error

Every "wedge" datum previously recorded here and in the memory
[[demo5-real-cause-lost-futex-wakeup-not-poller-starvation]] was produced with
a wall-clock timeout below the real boot time:

- OFF baseline "0/3": 180 s timeout — boot needs ~242 s.
- Fairness overlay "0/6", B=5: 180 s timeout.
- Sticky-wake overlay "0/3": 200 s timeout.

All three timeouts fell inside the ~90 s hpet0 pause, so the enforcer SIGKILLed
a still-progressing boot and recorded a false wedge. The "byte-identical 17869
bytes" was not evidence of a deterministic livelock — it was just the serial
length at the moment the crawl happens to pause, identical across runs because
the boot is deterministic *up to that point* and every run was killed there.

This reconfirms the pre-existing memory
[[demo5-icount-sleep-on-neutral-under-strict]], which already recorded that
under `--strict` demo5 has **no hard livelock** and "crawls to boot ~323–328s"
(that figure was under different load; ~242 s here). I should have heeded it
before concluding a wedge existed.

## Consequences for the two "fix" levers

Because there is no wedge under the shipped config on this host, neither lever
can be credited or discredited *against demo5*:

- **Sticky/pending futex-wake overlay** (`--sched-sticky-futex-wakes`, branch
  `claude/detcore-sticky-futex-wakes` @ `41ed79ce`, unit tests 3/3): remains a
  legitimate **default-off research** overlay for genuine lost-wakeup
  interleavings. Its earlier "does not green demo5" note is retracted as an
  artifact; it simply has no demonstrated demo5 relevance either way.
- **Bounded service-lead fairness/aging overlay** (Hermit PR #1386,
  `--sched-fairness-budget=B`): unchanged status — default-off, labeled,
  research-only, with the unresolved ON-path determinism hole (#140). No demo5
  claim, positive or negative, is supported.
- **vtime-jump-over-unproductive-pollers** (Option A `step2d_handle_empty_queue`
  / Option B per-turn `add_scheduler_time` suppression): both were previously
  and independently refuted for demo5
  ([[demo5-cause-B-vtime-suppression-empirical-refutation]]); with no wedge to
  fix, there is nothing here for a vtime-jump change to address. **Do not
  prototype it for demo5.**

## The real operational property of demo5

demo5's actual property under the shipped config is **slowness**, not a livelock:
a full-Linux QEMU boot under `hermit --strict` takes ~4 min of wall time on a
quiet host, dominated by TCG emulation, and is load-sensitive (prior findings
[[qemu-demos-host-provisioning-devbig014]], [[demo5-multisect-*]] record that
under heavy contention it can fail; those are load/host-capacity failures and a
distinct earlier-than-hpet0 signature, not the deterministic code livelock this
file wrongly asserted). The actionable levers are therefore:

1. an adequate wall budget (≥ ~300 s) plus host headroom — the demo's own
   `QEMU_TIMEOUT=600` default is already sufficient; only the validation
   harness was mis-budgeted, and
2. boot-time reduction (out of scope of any scheduler fairness/futex change).

There is no evidence for a core-DetCore scheduling defect behind demo5 on this
host, and no core scheduling change is warranted by this investigation.

## Reproduction / enforcer

Out-of-container `ignored/fairness-val/boot_sweep.py` (own pgid via
`start_new_session=True`, outer wall-clock timeout, SIGKILL to the pgid on
timeout). **Use `--rcb on` for the canonical config and a timeout ≥ 300 s** (the
default 150 s and the ad-hoc 180–200 s values used earlier are all too short and
will manufacture a false wedge). Do NOT use the in-container
`qemu_controller.py --timeout`; it is virtualized and trips on vtime skew before
`qmp.sock` exists ([[demo5-pmu-skid-refuted-target-timeslice-not-fix]]).

```bash
python3 ignored/fairness-val/boot_sweep.py --rcb on --timeout 600 --reps 5
```
