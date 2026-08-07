# 2026-08-05 Worktree Admin-Prune Incident Ledger

## Summary

At 2026-08-05 09:11:35 EDT (13:11:35Z), Markdown backticks embedded in a
double-quoted issue-creation shell command were evaluated as command
substitutions. One substitution invoked:

```text
scripts/release-worktree.rs --slot drainer-1 --clean
```

Hermit removal refused because the worktree contained submodules. The release
loop nevertheless continued and ran repository-wide `git worktree prune` for
the missing Reverie and LiteInst2 children. It removed 19 Reverie and 21
LiteInst2 administrative records before the script exited without saving the
slot registry. The issue wrapper then rejected the destination, so no issue was
created.

All 40 recorded paths were already absent. No live physical checkout, Git
object, branch, or process was deleted by this incident. The registered Hermit
checkout survived clean at
`65a3c1e0d65f31597bbcca3844ef319faca8f36c`. Cleanup, GC, pruning, and release
retries remain prohibited until the release tool is made target-scoped.

## Immutable evidence

- Local session transcript:
  `/home/newton/.codex/sessions/2026/08/05/rollout-2026-08-05T04-34-42-019fd1b4-4feb-7611-8c8d-486bc80681a8.jsonl`
- Transcript SHA-256:
  `7730801698a64a52cc7024f8357d41fd64cb12f9e9aeb68faf926af55a2bc2b5`
- Audited size: 9,132,078 bytes.
- Pre-prune inventory: 13:06:36Z.
- Prune dry-run: 13:08:24Z.
- Accidental invocation: 13:11:35Z.
- Post-prune zero-entry check: 13:11:50Z.
- Audit mode was read-only; no admin record was recreated.

## Exact deleted admin-name sets

The name sets are exact. Only `reverie13` has a proven path-to-admin pairing.

```text
Reverie:
reverie36 reverie1 reverie reverie2 reverie3 reverie4 reverie6
reverie7 reverie8 reverie9 reverie10 reverie11 reverie5 reverie12
reverie13 reverie14 reverie15 reverie16 reverie17

LiteInst2:
liteinst2-ci liteinst2-profiler-phase1 liteinst2-randinst
liteinst2-profiler-phase2 liteinst2-byte-validation
liteinst2-trampoline-arena liteinst2-stress-test
liteinst2-stress-baseline liteinst2-overnight liteinst2-pr7-fix
liteinst21 liteinst2 liteinst22 liteinst24 liteinst25 liteinst26
liteinst27 liteinst28 liteinst29 liteinst211 liteinst23
```

Known direct association:

```text
reverie13|worktrees/drainer-1/reverie|d805a990236e781602fb7382cd0a73dc37b7ea19|perf/coordinator-rpc-guest-side-trim
```

## Exact pre-prune path and HEAD ledger

Paths are relative to `/home/newton/work/dev-hermit` unless absolute.

```text
reverie|worktrees/270/reverie|121a56467d1c8ac42ccb036c740d9c391b118c5b
reverie|worktrees/271/reverie|043aa00d9ea2dc27fa0e9e887c14f47de486bc68
reverie|worktrees/272/reverie|e5900c737cfa73af711da1c64c19278e023b2736
reverie|worktrees/273/reverie|36731785a44f36b5d47ef8e59553013abe7d1abd
reverie|worktrees/274/reverie|6163cb5eedff5d7e39df26fbdf73aa0d7d80de27
reverie|worktrees/ci/reverie|043aa00d9ea2dc27fa0e9e887c14f47de486bc68
reverie|worktrees/dbi/reverie|9244df1f986b483996da92eb7ede6ea5ca887678
reverie|worktrees/debug30/reverie|043aa00d9ea2dc27fa0e9e887c14f47de486bc68
reverie|worktrees/drainer-1/reverie|d805a990236e781602fb7382cd0a73dc37b7ea19
reverie|worktrees/drainer-2/reverie|d045caf1798c91f4f48aa77e9a509e3646de8f84
reverie|worktrees/drainer-3/reverie|d5c6a3facdb8aeb1e1e08df778e72d4395d4717d
reverie|worktrees/drainer-4/reverie|73695ea3ae09b52733155358a987e1aa87bc8958
reverie|worktrees/drainer-5/reverie|6b37cd1415b4820ce4941dee3c05d2d76c421afa
reverie|worktrees/e9patch/reverie|1844eec62800b088c88e0e0167fa376b25fd1b1c
reverie|worktrees/kvm/reverie|9bb0a6817b3c26def1d24cae709bf8f4b4bd4265
reverie|worktrees/linux/reverie|a70eee7c03e09218c354adfae1d83dc4356c2450
reverie|worktrees/liteinst/reverie|05681145fe627d9d0b71bf65a44f6e04a403201c
reverie|worktrees/sabre/reverie|125bc125f0b7766498487c0ba9f3b786f9d651f3
reverie|/tmp/dev-hermit-worktrees/slot344/reverie|aa1fe2a4482706ca31460947369f1724250421d4
liteinst2|worktrees/270/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/271/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/273/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/ci/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/dbi/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/debug30/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/e9patch/liteinst2|b21b248294e6cbed1dd4a7ff01e7264c06741882
liteinst2|worktrees/kvm/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/linux/liteinst2|b21b248294e6cbed1dd4a7ff01e7264c06741882
liteinst2|worktrees/sabre/liteinst2|261671a655086c0d3f4642b24718bb64586c1384
liteinst2|worktrees/slot241/liteinst2|cbe7f1116d5840aa00c4d76d1ca45e81d7d28891
liteinst2|/tmp/liteinst2-byte-validation|40bcd3c69084d386284cce0ef16f59ffbb32e749
liteinst2|/tmp/liteinst2-ci|d7ac4e57d6259406fe08528d21645b47f7f8a221
liteinst2|/tmp/liteinst2-overnight|ca71db4ce44cd80d14ba31f6791899d8078a1ceb
liteinst2|/tmp/liteinst2-pr7-fix|2d1ca2f94fd191770296f2eb972228cf62b8f3cd
liteinst2|/tmp/liteinst2-profiler-phase1|d9f9393f2b082cdd19e0f085f4622026b2ba52d6
liteinst2|/tmp/liteinst2-profiler-phase2|c1aa8f26d8eb54d3be712247fff10a957eee2444
liteinst2|/tmp/liteinst2-randinst|a64eb5cd15076dc80f93cc96982d445e4f83e905
liteinst2|/tmp/liteinst2-stress-baseline|a64eb5cd15076dc80f93cc96982d445e4f83e905
liteinst2|/tmp/liteinst2-stress-test|77e30d9ee8b68f94e66f7902b4cb18250e14d13d
liteinst2|/tmp/liteinst2-trampoline-arena|60363a40db50a86926d6925f6ecaa53d5470c2f9
```

## Reachability and preservation

All 40 path/HEAD records still resolved after the incident, representing 29
unique commit objects. Before recovery, 38 of 40 records—27 of 29 unique
commits—were already remotely contained. The two local-only Reverie commits
were pushed without force to:

```text
d045caf1798c91f4f48aa77e9a509e3646de8f84 refs/heads/recovery/20260805-worktree-prune-pr221-pre-rebase
aa1fe2a4482706ca31460947369f1724250421d4 refs/heads/recovery/20260805-worktree-prune-pr156-original
```

LiteInst2 `ca71db4ce44cd80d14ba31f6791899d8078a1ceb` remains at
`refs/pull/10/head`; `2d1ca2f94fd191770296f2eb972228cf62b8f3cd`
remains at `refs/pull/11/head`.

## Process evidence

PIDs 2012549 and 2044402 survived. Both were already reparented to PID 1 with
deleted working directories and executables before the incident, and both
remain in shared scope `run-p2693323-i23452730.scope`. Do not kill either PID or
the shared scope without separate ownership authorization.

## Forensic limitations and disposition

The 39 other deleted admin-name-to-path pairings were not captured. Their
worktree-specific index and HEAD reflog state was not preserved in current Git
admin state and could not be reconstructed from the evidence audited; backup
or filesystem-undelete recovery was not attempted. Their physical paths had
already disappeared. Do not recreate stale admin directories: that would not
restore missing files or index state.

The release tool must be fixed before retrying any cleanup. It must remove only
an exact existing registered child, skip products recorded as `-`, refuse an
allocated-but-missing child, never call repository-wide `git worktree prune`,
and commit registry state only after exact-target removal succeeds.
