# btrfs userspace experiment helpers

Supporting tools for the 2026-07-28 btrfs userspace experiments:

- `mutate_image.py` applies the documented image mutations.
- `image_roundtrip.sh` exercises a btrfs-image round trip.
- `run_scoped.sh` runs one command in an isolated process group with a bounded
  timeout, recording its command and exit metadata.

Raw filesystem images are excluded by the parent binary-artifact guards.
Generated command transcripts stay in the gitignored `runs/` directory. The
durable findings and compact result tables live in the sibling experiment
directories that reference these helpers.
