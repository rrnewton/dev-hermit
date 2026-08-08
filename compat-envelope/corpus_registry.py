#!/usr/bin/env python3
"""The single source of truth for WHICH POPULATION a backend ratio was measured over.

WHY THIS FILE EXISTS
--------------------
`e9patch` reaches **20/20** on the dedicated corpus and **4/137 built** on the
shared full corpus. Those are the same backend, the same binary, and the same
day. A reader who sees either number alone will quote it as "e9patch reach", and
the first reads as near-complete coverage while the second reads as near-zero.

The number was never wrong. The POPULATION was unstated. This is the denominator
rule applied to WHICH SET, not to HOW MANY -- and unlike a bare count, a ratio
whose population is unstated is misleading in the most confident possible way,
because it already looks fully qualified.

So: a corpus is a NAMED thing with a recorded source, build recipe, and output
artifact. Every emitted ratio must carry one of these names. A ratio that cannot
name its corpus is refused at emission by `check_corpus_named.py` rather than
published and caveated later.

FIELDS
------
`source`        where the population is enumerated from. If this is env-
                overridable, `source_env` names the variable -- an overridable
                population is exactly the "glob that changed with the invocation
                directory" shape, so the override is recorded, not hidden.
`build`         how guests are compiled. This is load-bearing, not trivia:
                `-nostdlib -static -ffreestanding` puts the syscall in the main
                ELF (reachable by an AOT rewriter), while a dynamic build puts it
                in libc (not reachable). The build recipe is WHY the two e9patch
                numbers differ.
`size`          population size as measured on 2026-08-07, with how it was
                counted. A dated observation, not an invariant -- corpora grow.
`csv`           the artifact this corpus's cells are written to.
`markers`       strings that identify this corpus inside an emitted line or a
                CSV row, used by the guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Corpus:
    name: str
    source: str
    build: str
    size: str
    csv: Optional[str]
    why_it_differs: str
    source_env: Optional[str] = None
    markers: tuple[str, ...] = field(default_factory=tuple)


REGISTRY: dict[str, Corpus] = {
    "e9patch-dedicated": Corpus(
        name="e9patch-dedicated",
        source="hermit/tests/backend-parity/e9patch_corpus/*.c",
        build="-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie "
              "(collect-e9patch-compat.rs FREESTANDING_FLAGS)",
        size="20 .c guests (counted 2026-08-07 in worktrees/e9patch/hermit)",
        csv="compat-envelope/e9patch-scorecard.csv",
        why_it_differs="Freestanding and static, so the raw syscall sits in the main "
                       "ELF and an AOT rewriter can reach it. This is the corpus "
                       "e9patch scores 20/20 on.",
        source_env="--corpus",
        markers=("e9patch-dedicated", "e9patch_corpus", "e9patch-corpus"),
    ),
    "fullcorpus": Corpus(
        name="fullcorpus",
        source="compat-envelope/corpus/corpus-c.tsv + corpus/corpus-nonc.tsv",
        build="cc -std=c11 -O2 -g -Wall -Wextra -Werror (DYNAMIC; "
              "collect-fullcorpus.sh)",
        size="214 C + 21 non-C = 235 cells (wc -l corpus-c.tsv; grep -vc '^#' "
             "corpus-nonc.tsv, 2026-08-07)",
        csv="compat-envelope/fullcorpus-scorecard.csv",
        why_it_differs="Dynamically linked, so guests issue their syscalls from "
                       "libc and the main ELF has nothing for an AOT rewriter to "
                       "patch. This is the corpus e9patch reaches 4/137-built on. "
                       "A parity score here can be vacuous: the shared ptrace "
                       "runtime ran underneath.",
        source_env="CORPUS_C / CORPUS_NONC",
        markers=("fullcorpus", "full-corpus", "full corpus"),
    ),
    "ci-regression": Corpus(
        name="ci-regression",
        source="portable-CI e2e subset (lane=portable, ci=true)",
        build="per-manifest cflags",
        size="646 rows across several run_ids (wc -l scorecard.csv, 2026-08-07)",
        csv="compat-envelope/scorecard.csv",
        why_it_differs="A filtered subset, not the corpus. Headlining it as the "
                       "denominator is what produced the '28' scorecard that "
                       "REPORT.md later had to correct to 200.",
        markers=("ci-regression", "portable-CI subset", "portable-ci subset"),
    ),
    "reverie-examples": Corpus(
        name="reverie-examples",
        source="reverie example tools (collect-reverie-compat.rs)",
        build="reverie workspace build",
        size="12 rows (wc -l reverie-scorecard.csv, 2026-08-07)",
        csv="compat-envelope/reverie-scorecard.csv",
        why_it_differs="Measures Tool callback-count parity, a different "
                       "OBSERVABLE from stdout parity. Never average it with the "
                       "backend corpora.",
        markers=("reverie-examples", "reverie-scorecard"),
    ),
    "dbi-corpus": Corpus(
        name="dbi-corpus",
        source="hermit-manifest-plan reconstruction (collect-dbi-corpus.rs)",
        build="per-manifest cflags",
        size="202 tests (DBI-CORPUS-INGEST.md denominator, 2026-08-07)",
        csv="ignored/dbi-corpus-scorecard.csv",
        why_it_differs="Rebuilt from the manifest plan rather than from a tsv, so "
                       "it tracks the manifest and not corpus-c.tsv.",
        markers=("dbi-corpus",),
    ),
}

# Backend names a ratio can be scoped to. `native` is included because a
# native-vs-hermit ratio has exactly the same population problem.
BACKENDS = ("ptrace", "dbi", "kvm", "sabre", "liteinst", "e9patch", "native")


def all_markers() -> dict[str, str]:
    """marker string -> corpus name. Longest markers first at the call site."""
    out: dict[str, str] = {}
    for c in REGISTRY.values():
        for m in c.markers:
            out[m.lower()] = c.name
    return out


def resolve_for_csv(csv_path: str) -> Optional[Corpus]:
    """Which corpus does this scorecard CSV hold?

    Filename-keyed, and that is a PROXY, not an identity: nothing stops a caller
    renaming or concatenating files. It is offered so an emitter can default
    honestly, never so it can skip an explicit `--corpus-name`. Returns None when
    the path is not a registered artifact -- the caller must then REFUSE, because
    an unrecognised population is precisely the case this module exists for.
    """
    tail = csv_path.replace("\\", "/").split("/")[-1].lower()
    for c in REGISTRY.values():
        if c.csv and c.csv.split("/")[-1].lower() == tail:
            return c
    return None


def describe(name: str) -> str:
    """One line naming the corpus, its size, and its build -- for an emitter to
    print immediately above or beside its ratios."""
    c = REGISTRY.get(name)
    if c is None:
        raise KeyError(
            f"unregistered corpus {name!r}; known: {sorted(REGISTRY)}. "
            "Add it to corpus_registry.py rather than emitting an unnamed ratio."
        )
    return f"corpus={c.name}  population={c.size}  source={c.source}  build={c.build}"


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Show the registered measurement corpora.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--for-csv", help="resolve which corpus a scorecard CSV holds")
    ap.add_argument("--describe", help="one-line description of a corpus by name")
    a = ap.parse_args(argv)
    if a.describe:
        print(describe(a.describe))
        return 0
    if a.for_csv:
        c = resolve_for_csv(a.for_csv)
        if c is None:
            print(f"UNREGISTERED: {a.for_csv} matches no corpus in the registry",
                  flush=True)
            return 1
        print(describe(c.name))
        return 0
    if a.json:
        print(json.dumps({k: vars(v) for k, v in REGISTRY.items()}, indent=2, default=list))
        return 0
    for c in REGISTRY.values():
        print(f"{c.name}")
        print(f"  source : {c.source}" + (f"   (override: {c.source_env})" if c.source_env else ""))
        print(f"  build  : {c.build}")
        print(f"  size   : {c.size}")
        print(f"  csv    : {c.csv}")
        print(f"  why    : {c.why_it_differs}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
