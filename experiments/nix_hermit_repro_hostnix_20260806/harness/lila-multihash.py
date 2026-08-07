#!/usr/bin/env python3
"""lila-multihash.py — pull the CROSS-MACHINE nondeterministic candidate list
from Lila (reproducibility.nixos.social).

Lila collects NAR hashes for the same derivation output from several
independent builders. An output with more than one distinct `output_hash` is
non-reproducible *across those builders*. That is a superset filter, not the
answer: a cross-machine difference may be environmental (CPU features, nproc,
kernel) and therefore invisible to a same-host rebuild, exactly as happened for
nftables-1.1.6. But a build that bakes its own wall-clock time or a
`/dev/urandom` draw into the output differs across ANY two builds, including
two on this host, so the on-machine-nondeterministic set we can actually study
is contained in this list.

Output: TSV `output_name<TAB>store_path<TAB>n_distinct_hashes` on stdout.

Usage:
  http_proxy=http://fwdproxy:8080 https_proxy=$http_proxy \\
    python3 lila-multihash.py --evaluation 26 --jobs 24 > lila-multihash-eval26.tsv
"""

import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://reproducibility.nixos.social"


def get_json(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def attestations(digest_name):
    url = f"{BASE}/api/attestations/by-output/{digest_name}"
    try:
        rows = get_json(url)
    except Exception as e:  # network / 404 / rate limit
        return digest_name, None, str(e)
    hashes = {r["output_hash"] for r in rows}
    return digest_name, hashes, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evaluation", type=int, default=26)
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ev = get_json(f"{BASE}/api/evaluations/{args.evaluation}")
    paths = ev["output_paths"]
    if args.limit:
        paths = paths[: args.limit]
    basenames = [p.split("/nix/store/")[-1] for p in paths]
    print(f"# evaluation {args.evaluation}: {len(basenames)} outputs", file=sys.stderr)

    multi, single, errs = [], 0, 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, (name, hashes, err) in enumerate(ex.map(attestations, basenames)):
            if err is not None:
                errs += 1
                continue
            if hashes is None or len(hashes) <= 1:
                single += 1
                continue
            multi.append((name, len(hashes)))
            if i % 500 == 0:
                print(f"# .. {i}/{len(basenames)}", file=sys.stderr)

    print(f"# single-hash={single} multi-hash={len(multi)} errors={errs}", file=sys.stderr)
    for name, n in sorted(multi, key=lambda t: -t[1]):
        # basename is <32-char digest>-<pkgname>
        pkg = name.split("-", 1)[1] if "-" in name else name
        print(f"{pkg}\t/nix/store/{name}\t{n}")


if __name__ == "__main__":
    main()
