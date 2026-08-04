# Hermit validation run reconstruction: 2026-08-02

This table reconstructs the 20 retained `/tmp/hermit-validate.*.log` files
created on 2026-08-02 (America/Los_Angeles). Historical logs recorded the
checkout root, validation profile, and each completed gate's exit code and
duration. They did not record a commit, Git depth, final process exit, or CPU
time.

The recoverable commits below come from the checkout reflog at each log's birth
time. Git depth is `git rev-list --count <commit>`. Wall time is the log's file
birth-to-last-write interval, so it is a reconstruction rather than a timer
captured by the old harness. A run is `FAIL` when any completed gate failed,
`PASS` when every expected gate completed successfully, and `INCOMPLETE` when
the log contains no completed gate. Historical CPU time is unknown for every
row because neither the logs nor another retained artifact captured it.

| Started (PDT) | Log | Slot / checkout | Profile | Commit / Git depth | Result (completed gates) | Wall | CPU |
|---|---|---|---|---|---|---:|---:|
| 00:06:44 | `vs5jua` | `slot241` | `liteinst-compat-only` | unknown / unknown | PASS (3/3) | 186s | unknown |
| 00:13:41 | `Z6BIiK` | `slot241` | `liteinst-compat-only` | unknown / unknown | PASS (3/3) | 103s | unknown |
| 10:02:09 | `0KZGvS` | `primary` | `full` | `a14c18786adc2058e480ad8c252be1175fe356ef` / 1308 | FAIL (2/3) | 136s | unknown |
| 10:18:40 | `RXGMfx` | `main-recovery` | `full` | `51996c19be910df4a5d3e23ae574687dff80e714` / 1311 | FAIL (2/4) | 98s | unknown |
| 10:23:57 | `6I1eLM` | `primary` | `full` | `d45c2214f0675ace44a315d0bb29ff78b1983f14` / 1314 | FAIL (2/3) | 315s | unknown |
| 11:33:02 | `pmBEBy` | `primary` | `full` | `7fb1f94009140929f6ab2ec45756d7c563124cb6` / 1317 | FAIL (2/4) | 327s | unknown |
| 11:41:48 | `YTywIk` | `primary` | `rr-compat-only` | `fe048351c3a60889cd61cc17171006da58842da5` / 1318 | INCOMPLETE (0 gates) | 0s | unknown |
| 11:42:18 | `DUtV5g` | `primary` | `rr-compat-only` | `fe048351c3a60889cd61cc17171006da58842da5` / 1318 | FAIL (0/1) | 601s | unknown |
| 11:54:21 | `PBwpf2` | `primary` | `full` | `2a8d92e1b12b0a0610a65466883d77c5805421e6` / 1319 | FAIL (2/4) | 168s | unknown |
| 12:00:55 | `qbAezt` | `primary` | `full` | `1819b915e5ef163d4b47a9bc667ca82d80c2f1a4` / 1320 | FAIL (1/4) | 89s | unknown |
| 12:12:00 | `M02izy` | `primary` | `full` | `1819b915e5ef163d4b47a9bc667ca82d80c2f1a4` / 1320 | FAIL (2/4) | 544s | unknown |
| 12:14:01 | `868elt` | `primary` | `full` | `1819b915e5ef163d4b47a9bc667ca82d80c2f1a4` / 1320 | FAIL (2/4) | 328s | unknown |
| 12:22:33 | `kwZCkB` | `version-clean-validation` | `envelope-only` | `a6aeda47dca218deadb9b4f37a4398ca6a3bcb4e` / 1323 | PASS (1/1) | 59s | unknown |
| 14:23:28 | `dXqosl` | `primary` | `full` | `b90b3ba277ad4b2c4fce0f7b88521e87fc7ce407` / 1334 | FAIL (3/4) | 322s | unknown |
| 14:27:58 | `g7RjdD` | `primary` | `portable-strict-compat-only` | `b90b3ba277ad4b2c4fce0f7b88521e87fc7ce407` / 1334 | FAIL (0/1) | 23s | unknown |
| 14:30:34 | `pC7y7z` | `primary` | `full` | `090fe8b4fa583494f3c0d7fa28b82ee65f8e2f19` / 1337 | FAIL (3/4) | 47s | unknown |
| 16:37:26 | `3ap7Un` | `dbi` | `full` | `761e29504d2b6e283ae28185f9c2f636becab5d1` / 1344 | FAIL (0/4) | 22s | unknown |
| 16:41:13 | `mUtjeH` | `dbi` | `full` | `761e29504d2b6e283ae28185f9c2f636becab5d1` / 1344 | FAIL (2/3) | 191s | unknown |
| 18:46:38 | `KlQTE4` | `ci` | `full` | `db9eee72e3c6c3af74643ebe91ae6c39ae477d40` / 1346 | FAIL (3/4) | 543s | unknown |
| 18:54:52 | `5VVNJt` | `ci` | `portable-strict-compat-only` | `db9eee72e3c6c3af74643ebe91ae6c39ae477d40` / 1346 | FAIL (0/1) | 22s | unknown |

The two `slot241` worktrees were recreated after their runs, so their historical
reflogs no longer exist and their commit/depth cannot be recovered. The three
standalone checkout labels abbreviate the corresponding `/tmp` checkout roots.

## Going-forward record

`hermit/validate.sh` appends one JSON object per completed invocation to
`ignored/validate-run-ledger.jsonl` in the enclosing dev-hermit parent. The
parent already ignores `ignored/`, so the ledger cannot dirty either checkout.
`HERMIT_VALIDATE_LEDGER=/path/to/file.jsonl` overrides the destination.

Each record contains UTC start/finish timestamps, hostname, slot and checkout
root, profile, exact commit, Git depth, ahead/behind counts relative to
`origin/main`, overall result and exit code, check/failure counts, real/user/sys
seconds, the detailed log path, and a per-gate name/result/exit/real-seconds
array. Appends use `flock` when available so concurrent slot runs do not
interleave. Ledger write failure warns but does not change validation's result.
