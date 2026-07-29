#!/usr/bin/env python3
import collections
import csv
import pathlib
import random
import statistics


HERE = pathlib.Path(__file__).resolve().parent
TRIALS = 5000


with (HERE / "results.tsv").open() as source:
    rows = list(csv.DictReader(source, delimiter="\t"))

grouped = collections.OrderedDict()
for row in rows:
    grouped.setdefault(row["strategy"], []).append(row)

baseline = next(
    row["sig12"]
    for row in rows
    if row["strategy"] == "blind" and row["seed"] == "1"
)


def seeds_to_first(hits):
    if not any(hits):
        return None, None, None
    rng = random.Random(1234)
    indexes = list(range(len(hits)))
    firsts = []
    for _ in range(TRIALS):
        rng.shuffle(indexes)
        firsts.append(next(i + 1 for i, index in enumerate(indexes) if hits[index]))
    return (
        statistics.mean(firsts),
        statistics.median(firsts),
        sorted(firsts)[int(0.9 * len(firsts)) - 1],
    )


summary = []
for strategy, strategy_rows in grouped.items():
    signatures = [row["sig12"] for row in strategy_rows]
    hits = [signature != baseline for signature in signatures]
    mean, median, p90 = seeds_to_first(hits)
    observed_first = next(
        (int(row["seed"]) for row, hit in zip(strategy_rows, hits) if hit),
        None,
    )
    summary.append(
        {
            "strategy": strategy,
            "n": len(strategy_rows),
            "distinct": len(set(signatures)),
            "hits": sum(hits),
            "hit_rate": sum(hits) / len(hits),
            "observed_first": observed_first,
            "stf_mean": mean,
            "stf_median": median,
            "stf_p90": p90,
            "mean_ms": statistics.mean(int(row["elapsed_ms"]) for row in strategy_rows),
            "exits": collections.Counter(row["exit"] for row in strategy_rows),
        }
    )


def value(item):
    if item is None:
        return "never"
    if isinstance(item, float):
        return f"{item:.2f}"
    return str(item)


with (HERE / "summary.tsv").open("w", newline="") as output:
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(
        [
            "strategy",
            "n",
            "distinct",
            "hits",
            "hit_rate",
            "observed_first",
            "stf_mean",
            "stf_median",
            "stf_p90",
            "mean_ms",
            "exits",
        ]
    )
    for row in summary:
        writer.writerow(
            [
                row["strategy"],
                row["n"],
                row["distinct"],
                row["hits"],
                f'{row["hit_rate"]:.3f}',
                value(row["observed_first"]),
                value(row["stf_mean"]),
                value(row["stf_median"]),
                value(row["stf_p90"]),
                f'{row["mean_ms"]:.0f}',
                ",".join(f"{key}:{count}" for key, count in sorted(row["exits"].items())),
            ]
        )

lines = [
    "| strategy | N | distinct | hits | hit-rate | observed first | stf median | stf p90 | mean ms |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for row in summary:
    lines.append(
        "| {strategy} | {n} | {distinct} | {hits} | {rate:.1%} | {observed} | {median} | {p90} | {mean_ms:.0f} |".format(
            strategy=row["strategy"],
            n=row["n"],
            distinct=row["distinct"],
            hits=row["hits"],
            rate=row["hit_rate"],
            observed=value(row["observed_first"]),
            median=value(row["stf_median"]),
            p90=value(row["stf_p90"]),
            mean_ms=row["mean_ms"],
        )
    )

(HERE / "TABLE.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
