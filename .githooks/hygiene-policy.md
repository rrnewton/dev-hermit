# Repo hygiene policy (parent dev-hermit) — experiments/ + debug/

Commit **code + reports + machine-readable state**. Never commit raw giant logs,
boot artifacts, dumps, VM/kernel images, or binaries.

## Default practice: ignored/-first

Write anything you know you won't commit — raw logs, boot artifacts, scratch, big
outputs — **directly into an `ignored/` dir from the start**. `ignored/` (and
`**/ignored/`) is gitignored, so there is **no commit-time judgment call**: junk
never enters the index. Only reports, the ledger/notebook, and machine-readable
state (`*.json`, `NOTEBOOK.md`, small curated `.log`/`.csv`/`.md`) live tracked in
`experiments/<foo>/` and `debug/<bar>/`.

## What is hard-ignored (clearly never commit)

VM/disk images (`*.qcow2 *.img *.raw *.iso`), kernels/initrd (`bzImage vmlinux
initramfs*.cpio* *.cpio`), boot/run dirs (`boots-*/ boot-*/ runs/`), archives
(`*.tar* *.tgz *.gz *.zip *.zst`), binaries (`*.bin *.a *.o *.so`), core dumps and
`*.perf.data`. `*.log` is deliberately **not** blanket-ignored — small curated
logs may be tracked; put big/raw logs under `ignored/`.

## The size backstop (`.githooks/pre-commit`)

A pre-commit hook WARNS and BLOCKS when a staged file exceeds a soft size limit
(default 1024 KiB; `HERMIT_HYGIENE_MAX_KB` to tune). It is only a safety net for
when the ignored/-first default is missed. Override when you have verified the
file is a genuinely-needed curated report/state:

```
HERMIT_HYGIENE_OVERRIDE=1 git commit ...
```

Install (per clone; `core.hooksPath` is local config, not tracked):

```
scripts/setup-hooks.sh
```

## Per-directory tuning

Any `experiments/<foo>/` or `debug/<bar>/` may add its **own `.gitignore`** to tune
what lives-but-ignored locally (`dbg new-episode` scaffolds a starter one).

## VCS_MISSING.md (required per dir)

Every `experiments/<foo>/` and `debug/<bar>/` must carry a `VCS_MISSING.md`
describing what is NOT checked in (won't exist on a fresh clone): which artifacts
are missing, whether each is **regeneratable** (and how), and whether any tracked
code **depends on reading** it (which would fail on another machine). `dbg
new-episode` scaffolds it; `dbg vcs-check <episode>` lists the currently-ignored
paths so you can keep it current.

## Demo-touching commits (demos/**)

Separate hard gate: every runnable-demo change needs an adversarial green-demo
attestation before merge. See `demos/ADVERSARIAL-REVIEW-POLICY.md`, the CI job
`demo-review-gate`, and `.githooks/commit-msg` (checker: `scripts/check-demo-review.sh`).

## GitHub main health before push

`.githooks/pre-push` runs `scripts/github_main_health.py` before every parent
push. It polls current-main `push` workflow runs for `rrnewton/dev-hermit`,
`rrnewton/hermit`, and `rrnewton/reverie` through `with-proxy gh`. A red main or
query failure emits a hard warning. The hook deliberately does not block because
the pending push may be the repair, but the coordinator must not claim green
until the live report is green.

## Primary checkout freshness before commit

`.githooks/pre-commit` runs `scripts/primary_checkout.py check` before every
parent commit. It warns when `hermit/`, `reverie/`, or `liteinst2/` is detached
or differs from the repository's live `origin/main`. The warning is nonblocking
so a gitlink or tooling repair can still be committed. Run `make checkout-fresh`
to fetch, check out `main`, and fast-forward each clean primary; dirty primaries
are preserved and skipped with a warning.
