---
name: core-memory-parent-git-size-is-local-modules-not-history
description: "parent .git=1.7G is local modules/sl metadata NOT history; object store only 5.6M (CORE-MEMORY mirror of memory/parent-git-size-is-local-modules-not-history.md)"
---

# CORE-MEMORY: parent-git-size-is-local-modules-not-history

<!-- GENERATED MIRROR of core memory `parent-git-size-is-local-modules-not-history`. Source of truth is the memory
     file `parent-git-size-is-local-modules-not-history.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: parent-git-size-is-local-modules-not-history.md) -->
The dev-hermit parent `du -sh .git` reads ~1.7G, but this is **entirely
machine-local metadata, not committed history**: `.git/modules/reverie`≈1.4G
(local reverie clone shared across all worktree submodules) + `.git/sl`≈222M
(Sapling). The parent's actual committed object store is only **5.6M**
(`git count-objects -vH`). So a large parent `.git` is NOT repo bloat and needs
NO `git filter-branch`/history surgery (which would be forbidden on shared main
anyway). Largest blobs in ALL parent history are just text debug logs
(experiments/qemu-linux/runs/.../*.log, 1.5M/1.1M/0.4M).

Hazard caught 2026-07-26 (task impl-repo-hygiene-experiments, parent main
667fa56): `experiments/gvisor/` was a 433M vendored upstream clone with a 361M
nested `.git`, untracked but un-ignored — one `git add experiments/` from being
swallowed. Now gitignored. Convention: experiments record source URLs + commit
SHAs, never embed vendor clones; heavy artifacts go under `ignored/` /
`experiments/ignored/` (both gitignored). `.gitignore` now guards
`/experiments/**` against *.img/qcow2/raw/iso/bin/a/o/so/tar*/tgz/gz/zip/zst/
core/bzImage/vmlinux/initramfs*.cpio*. See [[worktree-cleanup-is-unsafe-for-agents]].
<!-- END CORE-MEMORY-MIRROR -->
