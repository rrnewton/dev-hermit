#!/usr/bin/env python3
"""Create a deterministic metadata-biased mutation of a Btrfs image."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--flips", default=32, type=int)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()

    shutil.copyfile(args.source, args.target)
    size = args.target.stat().st_size
    if size <= 64 * 1024:
        raise SystemExit("image is too small for Btrfs metadata mutation")

    # Bias toward the primary superblock and the first 8 MiB where these
    # compact corpus images store most tree/chunk metadata.
    upper = min(size, 8 * 1024 * 1024)
    rng = random.Random(args.seed)
    mutations: list[dict[str, int]] = []
    with args.target.open("r+b") as image:
        for _ in range(args.flips):
            offset = rng.randrange(64 * 1024, upper)
            image.seek(offset)
            old = image.read(1)
            if not old:
                raise SystemExit(f"short read at {offset}")
            bit = 1 << rng.randrange(8)
            image.seek(offset)
            image.write(bytes((old[0] ^ bit,)))
            mutations.append({"offset": offset, "bit": bit})

    print(
        json.dumps(
            {
                "seed": args.seed,
                "flips": args.flips,
                "source_sha256": sha256(args.source),
                "target_sha256": sha256(args.target),
                "mutations": mutations,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
