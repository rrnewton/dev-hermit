---
name: validate-sh-rr-compat-counter-conflict
description: "Historical Hermit rebase hotspot: derive record/replay compatibility counters from current source after merging both sides; never copy a dated count."
---

# Record/replay compatibility counter rebases

Some Hermit revisions couple an expected-count constant to a program-label
collection. When both main and a feature branch add programs, the collection may
merge while the scalar conflicts. Inspect the exact current `validate.sh` or its
Rust replacement before assuming this mechanism still exists.

If it does, preserve the union of intentional entries, derive the count from the
merged collection with the repository's current verifier, and update the scalar
to that derived count. Never choose a conflict side or copy the dated counts in
old task notes. Run the focused structural test and then obtain a new full
exact-head receipt because a rebase changes the SHA.

Landing authority remains `ci-hub validate-status` at the current head. Do not
re-fire historical workflows or infer validity from a label. This product fact
belongs in Hermit and should migrate there when its current implementation is
confirmed.
