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
