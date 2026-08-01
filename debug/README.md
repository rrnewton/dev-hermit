# debug/ — persistent debugging-episode infrastructure

A reusable "automated-scientist" scaffold for regression / determinism
investigations. Each investigation is a **debugging episode** = one subdirectory
here holding **machine-readable** state, plus a tiny CLI (`dbg`) that turns the
state of the search into a *query* instead of prose someone has to re-read.

This complements `experiments/` (durable reproducible evidence) and `ai_docs/`
(prose research). `experiments/` holds the raw runs and artifacts; an episode
here holds the **structured investigation**: hypotheses (with status +
reasoning), the evidence bound to them, and the **suspect** list (candidate
regressing commits/causes) with which ones remain open. Episodes *reference*
`experiments/` artifacts by path rather than copying them.

## Layout

```
debug/
  dbg                    # the CLI (python3 stdlib, no deps)
  SCHEMA.md              # the JSON schema for the three record types
  README.md              # this file
  <episode-slug>/        # one dir per debugging episode
    episode.json         # metadata: title, status, GOOD/BAD anchors, question
    hypotheses.json      # [{id, statement, reasoning, predicted_evidence,
                         #   status: open|confirmed|killed, verdict, evidence_ids}]
    evidence.json        # [{id, desc, hypotheses[], artifact, source}]
    suspects.json        # [{id, sha, subject, subsystem, files[], priority,
                         #   status: open|cleared|confirmed, reasoning}]
    NOTEBOOK.md          # AGENT-SYNTHESIZED curated prose synopsis (see below)
    .notebook-state.json # snapshot of the machine-readable state at last sync
```

## The lab notebook (curated prose, not generated)

`NOTEBOOK.md` is the **synopsis a new reader starts from** — a well-written,
agent-synthesized narrative of the whole investigation, **not** a mechanical dump
of the JSON. It is structured in three sections:

- **EXPLORED** — what is established, each claim with evidence pointers (`E..`,
  artifact paths).
- **INVALIDATED** — killed hypotheses / dead ends, each with the evidence *and*
  the adversarially-evaluable claim that would resurrect it.
- **FRONTIER** — open hypotheses and open suspects, with their supporting evidence
  and the decisive experiment that would resolve them.

**Workflow (synthesize-and-revise on every change).** The machine-readable JSON is
the ledger of record; the notebook is curated by an agent. On each change to
log/hypotheses/evidence/suspects: (1) `dbg changed <episode>` surfaces exactly
what changed since the last sync; (2) the agent *synthesizes* the new item into the
notebook **and re-reads the whole notebook for global consistency** (a good
synopsis, not an append); (3) `dbg notebook-sync <episode>` re-snapshots. The CLI
makes the delta easy to see; it never writes the prose.

## The CLI (`./debug/dbg`)

Read queries (add `--json` to any for machine-readable output):

```bash
./debug/dbg episodes                                  # list episodes + counts
./debug/dbg summary   <episode>                        # one-screen status
./debug/dbg hypotheses <episode> --status all -v       # ALL hypotheses + reasoning + verdict
./debug/dbg hypotheses <episode> --status open         # only unresolved
./debug/dbg hypotheses <episode> --status closed       # confirmed + killed
./debug/dbg suspects   <episode> --status all          # ALL suspects
./debug/dbg suspects   <episode> --open                # REMAINING OPEN suspects (regressor hunt)
./debug/dbg evidence   <episode> [--hypothesis Hn]
./debug/dbg show       <episode> <id|sha-prefix>       # full record as JSON
./debug/dbg notebook   <episode>                       # print the curated NOTEBOOK.md
./debug/dbg changed    <episode>                        # what changed since last notebook sync
./debug/dbg notebook-sync <episode>                     # re-snapshot AFTER revising NOTEBOOK.md
```

Write ops (persist back to the JSON):

```bash
./debug/dbg new-episode <slug> --title "..." --question "..."
./debug/dbg add-hypothesis <episode> --id Hn --statement "..." --reasoning "..." --predicted "..."
./debug/dbg set-hypothesis <episode> Hn --status confirmed|killed|open --verdict "..."
./debug/dbg add-suspect    <episode> --sha SHA --subject "..." --subsystem ... --files a,b --priority high
./debug/dbg set-suspect    <episode> <id|sha> --status open|cleared|confirmed --reasoning "..."
./debug/dbg add-evidence   <episode> --desc "..." --hypotheses H1,H6 --artifact PATH --source AGENT
```

## Workflow

1. `new-episode` with the GOOD/BAD anchors and the central question.
2. **Bootstrap suspects** from the bisect interval: `git log GOOD..BAD` filtered
   to the subsystem's core files, one `add-suspect` per commit (see
   `demo5-regression` — seeded from every scheduler/time commit in
   `2a7ca98..ae2565be`). This is the first thing to do because it can directly
   surface the regressor.
3. Add hypotheses; gather evidence into `experiments/`; bind it with
   `add-evidence`; `set-hypothesis` to confirmed/killed with a verdict.
4. As evidence rules commits out, `set-suspect ... --status cleared`; the goal
   is to drive `suspects --open` toward the true regressor (or to conclude the
   "regression" is a config/latent-exposure, not a single commit).

## Episodes

- **`demo5-regression`** — the demo5 QEMU-boot wedge under `--no-rcb-time`.
  Migrated from `experiments/demo5-rootcause-20260731/` (lead hermit-226 + fleet
  210/231/237/238). 7 hypotheses (H1–H4,H6 confirmed; H7 killed; H5 open on
  perf), 11 evidence items, and **20 open suspects** seeded from the
  scheduler/time commits in `2a7ca98..ae2565be` (8 high-priority). See
  `./debug/dbg summary demo5-regression`.

## Relationship to experiments/

`debug/` does **not** replace `experiments/` as an artifact store — raw runs,
logs, CSVs, and reproduction scripts stay in `experiments/` (and transient
material in `ignored/`/`scratch/`). `debug/` is the *structured index over an
investigation* that points at those artifacts. The task that created this
framed it as "replacing experiments/" for the **episode/ledger** role
specifically: the human-readable `ledger.md` prose is superseded by these
queryable JSON records; the evidence artifacts remain in `experiments/`.
