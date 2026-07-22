# AI Documents

Store durable text-based research, design notes, and handoff records here.
Keep generated binaries, images, archives, and disposable investigation output
in `scratch/` or external artifact storage; those formats are ignored here.

Current design and assessment documents:

- `architecture-overview.md`: current Hermit execution model, event
  interception, deterministic state, and the ptrace/SaBRe/KVM backend boundary.
- `container-deployment.md`: validated namespace, mount, seccomp, PMU, and
  container-runtime requirements with deployment tiers.
- `qemu-integration-status.md`: landed versus draft QEMU/TCG support, measured
  syscall surface, virtual-time root cause, and acceptance gates.
- `schedule-search-guide.md`: reproducible `hermit analyze` workflow,
  prerequisites, examples, interpretation, and known validity limits.
- `pr-status.md`: dated live PR and CI snapshot with review holds.
- `known-limitations.md`: consolidated current limitations and dependency-ordered
  future work.
- `hermit-v2-roadmap.md`: comprehensive product roadmap and evidence summary.
- `kvm_backend_design.md`: proposed Reverie KVM backend based on gVisor's
  Sentry and KVM platform.
- `sabre_backend_assessment.md`: build and interface assessment of the
  historical in-guest SaBRe backend.
- `qemu_vng_setup.md`: reproducible QEMU, KVM, and virtme-ng host setup and
  smoke-test procedure.
- `arbitrary-binary-matrix.md`: compatibility results across Hermit launch
  modes and representative dynamically linked workloads.
- `chaos-effectiveness.md`: measured chaos scheduling and PMU preemption
  effectiveness, iteration budgets, and race-finding recommendations.
- `intel-pmu-analysis.md`: Intel PMU event, precise-IP, skid-margin, and model
  coverage analysis for deterministic preemption.
- `nondeterministic-preemption-record-replay.md`: design for recording and
  exactly replaying nondeterministic preemption coordinates.
- `performance-profile.md`: runtime overhead measurements, flat hotspots, and
  an evidence-based optimization order.
- `sabre-determinism-analysis.md`: SaBRe determinism gap matrix and prioritized
  roadmap for syscall, instruction, scheduling, signal, and lifecycle parity.
- `scx-sim-replay-strategy.md`: scx-sim replay mechanisms and their
  applicability to Hermit's precise-timer design.
- `syscall-coverage-map.md`: complete x86-64 Detcore syscall classification,
  missing coverage, and implementation priorities.
