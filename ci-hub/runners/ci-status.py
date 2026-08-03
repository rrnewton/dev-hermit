#!/usr/bin/env python3
"""On-demand CI status report for the Hermit repos.

Non-mutating. Summarizes, per repo: self-hosted runner health, Actions queue
depth, and the most recent green run of each workflow. Open-PR CI
classification belongs to the pinned `agent-utils/pr-landing-planner` and is
exposed through `ci-hub/bin/pr-status`; it is intentionally not duplicated
here.

Usage:
    ./ci-status.py                 # default: rrnewton/hermit
    ./ci-status.py --all           # all three Hermit repos
    ./ci-status.py --repo owner/name [--limit 100]

gh is invoked through `$GH` (default: "with-proxy gh") so it works on the
development host behind the proxy without changing the machine-global gh account.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections import Counter

DEFAULT_REPO = "rrnewton/hermit"
ALL_REPOS = [
    "rrnewton/hermit",
    "rrnewton/reverie",
    "facebookexperimental/hermit",
]


def gh_json(args: list[str], gh_cmd: str):
    """Run a gh subcommand and parse JSON stdout. Returns None on failure."""
    cmd = shlex.split(gh_cmd) + args
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"  ! gh call failed ({exc.__class__.__name__}): {' '.join(args)}",
              file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"  ! gh returned {out.returncode}: {out.stderr.strip()[:200]}",
              file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def report_runners(repo: str, gh_cmd: str) -> None:
    data = gh_json(["api", f"repos/{repo}/actions/runners"], gh_cmd)
    if not data:
        print("  runners: (none registered at repo scope, or no access)")
        return
    runners = data.get("runners", [])
    total = data.get("total_count", len(runners))
    online = sum(1 for r in runners if r.get("status") == "online")
    busy = sum(1 for r in runners if r.get("busy"))
    idle = sum(1 for r in runners
               if r.get("status") == "online" and not r.get("busy"))
    print(f"  runners: total={total} online={online} idle={idle} busy={busy}")
    for r in runners:
        labels = ",".join(l["name"] for l in r.get("labels", []))
        job = "busy" if r.get("busy") else "idle"
        print(f"    - {r.get('name'):<24} {r.get('status'):<8} {job:<5} [{labels}]")
    if runners and idle == 0:
        print("    ^ NO IDLE RUNNER — every self-hosted job is queued behind these.")


def report_runs(repo: str, gh_cmd: str, limit: int) -> None:
    fields = "databaseId,workflowName,status,conclusion,headBranch,createdAt,displayTitle"
    data = gh_json(
        ["run", "list", "--repo", repo, "--limit", str(limit), "--json", fields],
        gh_cmd,
    )
    if data is None:
        print("  runs: (could not fetch)")
        return
    status_ct = Counter((r["status"], r["conclusion"]) for r in data)
    queued = sum(v for (s, _), v in status_ct.items()
                 if s in ("queued", "in_progress", "waiting", "pending"))
    print(f"  runs (last {len(data)}): "
          + ", ".join(f"{s or '-'}/{c or '-'}={v}"
                      for (s, c), v in status_ct.most_common()))
    print(f"  in-flight (queued+running): {queued}")

    # Last green per workflow.
    by_wf: dict[str, dict] = {}
    green_by_wf: dict[str, dict] = {}
    for r in data:
        wf = r["workflowName"]
        by_wf.setdefault(wf, r)  # data is newest-first
        if r["conclusion"] == "success" and wf not in green_by_wf:
            green_by_wf[wf] = r
    print("  last green per workflow:")
    for wf in sorted(by_wf):
        g = green_by_wf.get(wf)
        if g:
            print(f"    - {wf:<10} GREEN {g['createdAt']} "
                  f"({g['databaseId']}) {g['displayTitle'][:44]}")
        else:
            print(f"    - {wf:<10} NO GREEN in last {len(data)} runs "
                  f"(latest: {by_wf[wf]['status']}/{by_wf[wf]['conclusion'] or '-'})")


def report_repo(repo: str, gh_cmd: str, limit: int) -> None:
    print(f"\n================ {repo} ================")
    report_runners(repo, gh_cmd)
    report_runs(repo, gh_cmd, limit)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=DEFAULT_REPO,
                   help=f"repo to inspect (default: {DEFAULT_REPO})")
    p.add_argument("--all", action="store_true",
                   help="inspect all Hermit repos")
    p.add_argument("--limit", type=int, default=100,
                   help="how many recent runs to summarize (default: 100)")
    p.add_argument("--gh", default=None,
                   help="gh command (default: $GH or 'with-proxy gh')")
    args = p.parse_args(argv)

    import os
    gh_cmd = args.gh or os.environ.get("GH", "with-proxy gh")

    repos = ALL_REPOS if args.all else [args.repo]
    print(f"Hermit CI status — gh via: {gh_cmd!r}")
    for repo in repos:
        report_repo(repo, gh_cmd, args.limit)
    print("\n(For remediation options see ci-hub/runners/README.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
