# Strict and verify compatibility matrix

This is the consolidated result of the 2026-07-23 compatibility expansion
batches 1 through 42. It reports the commands recorded in TaskGraph by
`impl-strict-compat-expansion` (batch 1) and
`impl-strict-compat-batch2` through `impl-strict-compat-batch42`.
The results are historical measurements, not claims about an untested branch tip.

## Run context

- Assurance: PASS L2 means `hermit run --strict --verify` completed both runs
  and reported a bitwise-identical deterministic execution.
- Backend: ptrace (the default) for every Hermit result.
- Log level: default for verdicts; INFO was used only to diagnose failures.
- Relaxations: none. The two expected-nonzero batch 12 probes and one batch 1
  `diff` probe use `--verify-allow both`; that changes accepted guest exit
  status, not determinism.
- Binary: `target/release/hermit`. Evidence spans several explicitly recorded
  SHAs: early batches used `0b241392473aff32cf96bb1fcad330bbd0e2fed3`;
  batches 16, 18, and 24 used the vfork-fix branch at `929ba88`; batches 19
  and 20 used `9a492fe49845e0e72f66117f0a1c6a8894f3b7bf`; and batch 23 used
  `fbf1d395c4c5012e4247d9ccfa4f304e1a4ab9a4`. Other task notes recorded
  only a release-binary path or timestamp, so this report does not invent SHAs.
  Batches 28, 31, and 35 ran at `fbf1d395`; batch 36 built its fixtures in a
  slot but used the primary release Hermit binary. Batches 37 and 38 ran at
  `46836669bd6c2f7151fbe65c55f4ea5bd1440897`; batch 39 recorded only the
  primary release-binary path. Batch 40 ran at `46836669`; batch 41 ran at
  `0b241392473aff32cf96bb1fcad330bbd0e2fed3`; and batch 42 recorded only the
  primary release-binary path, so this report does not invent its SHA.
- Command column: commands omit the common
  `target/release/hermit run --strict --verify --` prefix unless a Hermit
  option such as `--verify-allow both` matters.
- Scope: strict-matrix counts below are command outcomes, including recorded
  controls and workarounds. FAIL means the command did not reach L2, whether
  because of nondeterminism, a hang, a guest error, host state, or harness policy.
  Record/replay results are reported separately and are not mixed into these totals.

## Summary

| Batch | Category | PASS L2 | FAIL | NOT RUN |
|---:|---|---:|---:|---:|
| 1 | Core utilities and compression | 14 | 2 | 0 |
| 2 | Network and IPC | 12 | 0 | 1 |
| 3 | Compilation | 4 | 2 | 0 |
| 4 | Multi-threaded | 5 | 1 | 0 |
| 5 | Database and structured data | 9 | 2 | 0 |
| 6 | Interpreters | 6 | 1 | 1 |
| 7 | Compression and archiving | 11 | 1 | 0 |
| 8 | Text processing | 12 | 0 | 0 |
| 9 | Math and file inspection | 11 | 0 | 0 |
| 10 | Process and system utilities | 5 | 8 | 0 |
| 11 | Real applications | 8 | 3 | 0 |
| 12 | Signals and edge cases | 11 | 0 | 0 |
| 13 | Complex pipelines and shell | 8 | 2 | 0 |
| 14 | Networking inspection | 9 | 2 | 0 |
| 15 | Multithreaded compute and Python | 4 | 3 | 1 |
| 16 | C compilation pipelines | 9 | 4 | 0 |
| 17 | Language test frameworks | 1 | 2 | 0 |
| 18 | Larger compiled projects | 4 | 2 | 0 |
| 19 | Containers and system identity | 10 | 1 | 0 |
| 20 | Crypto and randomness | 9 | 1 | 0 |
| 21 | Scripting languages | 10 | 1 | 0 |
| 22 | JVM | 5 | 1 | 0 |
| 23 | Structured data processing | 6 | 3 | 0 |
| 24 | Archive and packaging | 7 | 0 | 0 |
| 25 | System administration and binary inspection | 9 | 1 | 1 |
| 26 | Editors and text output | 10 | 0 | 0 |
| 27 | Math and numeric tools | 7 | 3 | 0 |
| 28 | Filesystem tools | 10 | 1 | 0 |
| 29 | Network clients | 10 | 1 | 0 |
| 30 | Identity and permissions | 9 | 2 | 0 |
| 31 | Time and date | 8 | 0 | 1 |
| 32 | Process control | 8 | 1 | 0 |
| 33 | C++ programs | 5 | 0 | 0 |
| 34 | Web operations | 3 | 3 | 0 |
| 35 | Parallel execution | 7 | 1 | 0 |
| 36 | Hermit test fixtures | 8 | 0 | 0 |
| 37 | Larger real applications | 5 | 2 | 0 |
| 38 | Database and data processing | 7 | 2 | 0 |
| 39 | System information | 6 | 3 | 0 |
| 40 | Signal handling | 9 | 0 | 0 |
| 41 | Inter-process communication | 5 | 2 | 0 |
| 42 | Math and science | 7 | 2 | 0 |
| **Total** | | **323** | **66** | **5** |

## Core utilities

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 1 | wc | `/usr/bin/wc lines.txt` | PASS L2 | 1 | Stable fixture. |
| 2 | sort | `/usr/bin/sort fruits.txt` | PASS L2 | 1 | Stable fixture and locale. |
| 3 | uniq | `/usr/bin/uniq sorted.txt` | PASS L2 | 1 | Stable fixture. |
| 4 | head | `/usr/bin/head -3 lines.txt` | PASS L2 | 1 | |
| 5 | tail | `/usr/bin/tail -3 lines.txt` | PASS L2 | 1 | The known tail problem is record/replay-specific. |
| 6 | find | `/usr/bin/find srcdir` | PASS L2 | 1 | Local fixture tree. |
| 7 | tee | `/usr/bin/tee scratch/teeout.txt < lines.txt` | PASS L2 | 1 | |
| 8 | xargs | `/usr/bin/xargs /usr/bin/echo < fruits.txt` | PASS L2 | 1 | |
| 9 | diff, identical files | `/usr/bin/diff lines.txt lines_copy.txt` | PASS L2 | 1 | Guest exits 0. |
| 10 | diff, differing files | `/usr/bin/diff lines.txt <differing-file>` | FAIL | 1 | Expected guest exit 1 is rejected by the default `--verify-allow=success` before run 2; the source note did not retain the second filename. |
| 11 | diff, differing files | `run --strict --verify --verify-allow both -- /usr/bin/diff lines.txt <differing-file>` | PASS L2 | 1 | Correct harness policy for expected exit 1. |

## Network and IPC

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 12 | env | `env` | PASS L2 | 2 | |
| 13 | printenv | `printenv` | PASS L2 | 2 | |
| 14 | date | `date` | PASS L2 | 2 | Virtualized output: 2021-12-31 23:59:59 UTC. |
| 15 | hostname | `hostname` | PASS L2 | 2 | Virtualized as `hermetic-container.local`. |
| 16 | uname | `uname -a` | PASS L2 | 2 | |
| 17 | pipe | `bash -c 'echo hello \| wc -c'` | PASS L2 | 2 | Two-process pipe. |
| 18 | dd and sha256sum | `bash -c 'dd if=/dev/zero bs=1024 count=10 \| sha256sum'` | PASS L2 | 2 | Two-process pipe. |
| 19 | curl | `curl --version` | PASS L2 | 2 | No external network. |
| 20 | wget | `wget --version` | PASS L2 | 2 | No external network. |
| 21 | nc | `nc -h` | PASS L2 | 2 | No external network. |
| 22 | curl loopback connect | `curl http://127.0.0.1:9/` | PASS L2 | 2 | Wrapper accepts the expected refused-connect exit; the source note did not retain its exact wrapper text. |
| 23 | nc loopback connect | `nc -z 127.0.0.1 9` | PASS L2 | 2 | Wrapper accepts the expected refused-connect exit; socket/connect path is deterministic. |
| 24 | socat | `socat` | NOT RUN | 2 | Not installed. External fetches were intentionally excluded: the host had no direct egress and changing external networks are outside Hermit's determinism contract. |

## Compilation

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 25 | Python version | `/usr/bin/python3 -c 'import sys; print(sys.version)'` | PASS L2 | 3 | Stock interpreter. |
| 26 | make | `make -s -C wd` | PASS L2 | 3 | Sequential Makefile with one shell child. |
| 27 | GNU as | `as add.s -o /tmp/add.o` | PASS L2 | 3 | Single-process assembler. |
| 28 | GNU ld | `ld add.o -o /tmp/add_linked` | PASS L2 | 3 | Single-process linker. |
| 29 | gcc | `gcc -o /tmp/hc hello.c` | FAIL | 3 | Parent-versus-vfork-child scheduling order diverges, then child RNG seed assignment diverges. |
| 30 | rustc | `rustc --edition 2021 -o /tmp/hrs hello.rs` | FAIL | 3 | Same fork/clone/vfork scheduling class across codegen/linker children. |

## Multi-threaded

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 31 | C pthread counter | `./pth_counter` | PASS L2 | 4 | Four threads, mutex, counter 400000. |
| 32 | C condition variable | `./pth_condvar` | PASS L2 | 4 | Producer/consumer, result 42. |
| 33 | Rust threads | `./rs_threads` | PASS L2 | 4 | Four threads and `Arc<Mutex>`. |
| 34 | Go goroutines | `./go_routines` | PASS L2 | 4 | Four goroutines, WaitGroup and mutex. |
| 35 | CPython threads | `/usr/bin/python3.9 py_threads.py` | PASS L2 | 4 | Four threads and Lock, counter 200000. |
| 36 | Meta Python threads | `/usr/local/bin/python3 py_threads.py` | FAIL | 4 | Startup reads live `/proc/self` memory statistics; the failure also reproduces single-threaded. |

## Database and structured data

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 37 | sqlite3 | `sqlite3 :memory: 'CREATE TABLE t(x); INSERT INTO t VALUES(1),(2),(3); SELECT sum(x) FROM t;'` | PASS L2 | 5 | Plain strict output is 6. |
| 38 | Meta Python JSON | `python3 -c 'import json; print(json.dumps({"a":1,"b":[2,3]}))'` | FAIL | 5 | Meta Python runtime threads diverge despite identical guest stdout. |
| 39 | Meta Python hashlib | `python3 -c 'import hashlib; print(hashlib.sha256(b"hello").hexdigest())'` | FAIL | 5 | Same Meta Python runtime cause. |
| 40 | awk | `awk '{sum+=$1} END{print sum}' nums.txt` | PASS L2 | 5 | Output 100. |
| 41 | sed | `sed 's/foo/bar/g' text.txt` | PASS L2 | 5 | |
| 42 | bc | `bc -l pi.bc` | PASS L2 | 5 | File input avoids a pipeline hang; output 3.14159265358979323844. |
| 43 | OpenSSL | `openssl dgst -sha256 hash-input.txt` | PASS L2 | 5 | OpenSSL 3.5.7. |
| 44 | jq | `jq '.b[1]' data.json` | PASS L2 | 5 | jq 1.6. |
| 45 | xxd | `xxd hex-input.txt` | PASS L2 | 5 | |
| 46 | CPython JSON control | `/usr/bin/python3.9 -c 'import json; print(json.dumps({"a":1,"b":[2,3]}))'` | PASS L2 | 5 | Proves JSON is not the failure source. |
| 47 | CPython hashlib control | `/usr/bin/python3.9 -c 'import hashlib; print(hashlib.sha256(b"hello").hexdigest())'` | PASS L2 | 5 | Proves hashlib is not the failure source. |

## Interpreters

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 48 | Ruby | `/usr/bin/ruby -e 'puts (1..10).reduce(:+)'` | FAIL | 6 | Native command fails identically because the host RubyGems packaging cannot load `RbConfig`. |
| 49 | Ruby, gems disabled | `/usr/bin/ruby --disable-gems -e 'puts (1..10).reduce(:+)'` | PASS L2 | 6 | Host-packaging workaround. |
| 50 | Lua | `/usr/bin/lua -e 'print(math.pi)'` | PASS L2 | 6 | Lua 5.4.4. |
| 51 | Node.js | `/bin/node -e 'console.log(JSON.stringify({a:1}))'` | PASS L2 | 6 | Real Node 16.20.2 ELF, not the wrapper. |
| 52 | Bash | `/usr/bin/bash -c 'for i in 1 2 3; do echo $i; done'` | PASS L2 | 6 | |
| 53 | Dash | `/bin/dash -c 'echo hello world'` | NOT RUN | 6 | Not installed; `/bin/sh` is Bash. |
| 54 | CPython PID | `/usr/bin/python3.9 -c 'import os; print(os.getpid())'` | PASS L2 | 6 | Stable virtual PID. |
| 55 | Perl | `/usr/bin/perl -e 'use POSIX; print strftime("%Y", localtime(0)), "\n"'` | PASS L2 | 6 | Perl 5.32.1. |

## Compression and archiving

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 56 | gzip | `/usr/bin/gzip -n -c numbers.txt` | PASS L2 | 1 | `-n` removes gzip name/time metadata. |
| 57 | gunzip | `/usr/bin/gunzip -c fixture.gz` | PASS L2 | 1 | |
| 58 | tar create | `/usr/bin/tar -cf scratch/out.tar srcdir` | PASS L2 | 1 | |
| 59 | tar extract | `/usr/bin/tar -xf fixture.tar -C scratch` | FAIL | 1 | Guest euid 0 makes tar restore an unmapped archived uid/gid; guest exits 1. |
| 60 | tar extract, no owner restore | `/usr/bin/tar --no-same-owner -xf fixture.tar -C scratch` | PASS L2 | 1 | User-namespace workaround. |
| 61 | bzip2 round trip | `/bin/sh -c 'printf "hermit strict compatibility batch 7\n" \| /usr/bin/bzip2 -c \| /usr/bin/bzip2 -dc'` | PASS L2 | 7 | 2402/2402 messages. |
| 62 | xz round trip | `/bin/sh -c 'printf "hermit strict compatibility batch 7\n" \| /usr/bin/xz -c \| /usr/bin/xz -dc'` | PASS L2 | 7 | 3876/3876 messages. |
| 63 | zstd round trip | `/bin/sh -c 'printf "hermit strict compatibility batch 7\n" \| /usr/bin/zstd -q -c \| /usr/bin/zstd -q -d -c'` | PASS L2 | 7 | 3877/3877 messages. |
| 64 | tar default create | `/usr/bin/tar cf /tmp/test.tar /etc/hostname` | FAIL | 7 | Default owner-name NSS lookup reaches a host-timed AF_UNIX poll divergence. |
| 65 | tar numeric owner | `/usr/bin/tar --numeric-owner -cf /tmp/test.tar /etc/hostname` | PASS L2 | 7 | Avoids the NSS trigger; 1278/1278 messages. |
| 66 | zip and unzip | `/bin/sh -c 'rm -f /tmp/hermit-batch7.zip && /usr/bin/zip -q /tmp/hermit-batch7.zip /etc/hostname && /usr/bin/unzip -p /tmp/hermit-batch7.zip etc/hostname'` | PASS L2 | 7 | 3620/3620 messages. |
| 67 | cpio | `/bin/sh -c 'cd / && printf "etc/hostname\n" \| /usr/bin/cpio -o --quiet -H newc \| /usr/bin/cpio -i --quiet --to-stdout etc/hostname'` | PASS L2 | 7 | 3366/3366 messages. |
| 68 | sha256sum | `/usr/bin/sha256sum /etc/hostname` | PASS L2 | 7 | 1015/1015 messages. |
| 69 | sha512sum | `/usr/bin/sha512sum /etc/hostname` | PASS L2 | 7 | 1015/1015 messages. |
| 70 | md5sum | `/usr/bin/md5sum /etc/hostname` | PASS L2 | 7 | 1015/1015 messages. |
| 71 | base64 | `/bin/sh -c '/usr/bin/base64 /etc/hostname \| /usr/bin/base64 -d'` | PASS L2 | 7 | 3152/3152 messages. |
| 72 | diff | `/bin/sh -c 'printf "alpha\n" > /tmp/hermit-diff-a; printf "alpha\nbeta\n" > /tmp/hermit-diff-b; /usr/bin/diff /tmp/hermit-diff-a /tmp/hermit-diff-b; rc=$?; test "$rc" -eq 1'` | PASS L2 | 7 | Wrapper converts the expected difference into success; 2286/2286 messages. |

## Text processing

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 73 | cut | `/usr/bin/bash -c '/usr/bin/cut -d: -f1 /etc/passwd \| /usr/bin/head -5'` | PASS L2 | 8 | 2068/2068 messages. |
| 74 | paste | `/usr/bin/bash -c 'd=$(mktemp -d); printf "a\nb\n" > "$d/left"; printf "1\n2\n" > "$d/right"; /usr/bin/paste "$d/left" "$d/right"'` | PASS L2 | 8 | 2232/2232 messages. |
| 75 | comm | `/usr/bin/bash -c 'd=$(mktemp -d); printf "alpha\nbeta\n" > "$d/left"; printf "beta\ngamma\n" > "$d/right"; /usr/bin/comm "$d/left" "$d/right"'` | PASS L2 | 8 | 2232/2232 messages. |
| 76 | join | `/usr/bin/bash -c 'd=$(mktemp -d); printf "1 alice\n2 bob\n" > "$d/names"; printf "1 admin\n2 user\n" > "$d/roles"; /usr/bin/join "$d/names" "$d/roles"'` | PASS L2 | 8 | 2232/2232 messages. |
| 77 | expand | `/usr/bin/bash -c 'printf "alpha\tbeta\n" \| /usr/bin/expand -t 4'` | PASS L2 | 8 | 1529/1529 messages. |
| 78 | unexpand | `/usr/bin/bash -c 'printf "alpha   beta\n" \| /usr/bin/unexpand -a -t 4'` | PASS L2 | 8 | 1529/1529 messages. |
| 79 | fold | `/usr/bin/bash -c 'printf "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n" \| /usr/bin/fold -w 40'` | PASS L2 | 8 | 1543/1543 messages. |
| 80 | fmt | `/usr/bin/bash -c 'printf "Hermit formats this deliberately long deterministic paragraph into narrow lines for the compatibility probe.\n" \| /usr/bin/fmt -w 40'` | PASS L2 | 8 | 1543/1543 messages. |
| 81 | nl | `/usr/bin/bash -c 'printf "red\ngreen\nblue\n" \| /usr/bin/nl -ba'` | PASS L2 | 8 | 1537/1537 messages. |
| 82 | rev | `/usr/bin/bash -c 'printf "Hermit\ndeterminism\n" \| /usr/bin/rev'` | PASS L2 | 8 | 1541/1541 messages. |
| 83 | tac | `/usr/bin/bash -c 'printf "first\nsecond\nthird\n" \| /usr/bin/tac'` | PASS L2 | 8 | 1581/1581 messages. |
| 84 | split | `/usr/bin/bash -c 'd=$(mktemp -d); printf "one\ntwo\nthree\nfour\nfive\n" > "$d/input"; /usr/bin/split -l 2 "$d/input" "$d/part-"; /usr/bin/cat "$d"/part-*'` | PASS L2 | 8 | 3001/3001 messages. |

## Math and file inspection

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 85 | factor | `factor 123456` | PASS L2 | 9 | |
| 86 | seq | `seq 1 100` | PASS L2 | 9 | |
| 87 | expr | `expr 2 + 3` | PASS L2 | 9 | |
| 88 | dc | `dc -e '2 3 + p'` | PASS L2 | 9 | |
| 89 | numfmt | `numfmt --to=iec 1048576` | PASS L2 | 9 | |
| 90 | od | `od -An -tx1 DATA` | PASS L2 | 9 | Stable 53-byte fixture outside host `/tmp`. |
| 91 | hexdump | `hexdump -C DATA` | PASS L2 | 9 | |
| 92 | strings | `strings DATA` | PASS L2 | 9 | |
| 93 | file | `file DATA` | PASS L2 | 9 | |
| 94 | stat | `stat DATA` | PASS L2 | 9 | Stable metadata across both runs. |
| 95 | du | `du -b DATA` | PASS L2 | 9 | |

## Process and system utilities

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 96 | ps | `ps aux` | FAIL | 10 | Hermit's VSZ/RSS output differs between runs because live procfs memory fields are exposed. |
| 97 | whoami | `whoami` | PASS L2 | 10 | 562/562 messages. |
| 98 | id | `id` | FAIL | 10 | Stateful NSS/nscd cache path differs between runs. |
| 99 | groups | `groups` | FAIL | 10 | Guest supplementary gid 65534 has no NSS name; first run exits 1. |
| 100 | uptime | `uptime` | PASS L2 | 10 | 2492/2492 messages. |
| 101 | free | `free -m` | FAIL | 10 | Live procfs used/free/cache/available values differ. |
| 102 | df | `df -h` | FAIL | 10 | Host has disconnected `/mnt/xarfuse` endpoints; native command also exits 1. |
| 103 | mount | `mount` | PASS L2 | 10 | 802/802 messages. |
| 104 | lsof | `lsof` | FAIL | 10 | Hermit was killed with exit 137 during run 1; cause not established. |
| 105 | strace | `strace -c /bin/true` | FAIL | 10 | Nested `PTRACE_TRACEME` is not permitted under the ptrace backend. |
| 106 | time | `/usr/bin/time /bin/true` | PASS L2 | 10 | 1037/1037 messages. |
| 107 | timeout | `timeout 1 sleep 0.1` | FAIL | 10 | Host-wall-clock SIGALRM wins before the virtual 0.1-second sleep completes; exits 124. |
| 108 | nice | `nice -n 5 /bin/true` | PASS L2 | 10 | 675/675 messages. |

## Real applications

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 109 | Git init/add/status | `/bin/sh -c 'rm -rf /tmp/hermit-git-test && /usr/local/bin/git init /tmp/hermit-git-test && cd /tmp/hermit-git-test && /usr/local/bin/git add . && /usr/local/bin/git status'` | FAIL | 11 | Killed with exit 137 in run 1 after producing multi-gigabyte logs. |
| 110 | Git log | `/usr/local/bin/git log --oneline -5` | FAIL | 11 | A 15-second outer timeout expired in run 1 with exit 124; never reached L2. |
| 111 | Git diff | `/usr/local/bin/git diff --stat 'HEAD~1'` | FAIL | 11 | A 15-second outer timeout expired in run 1 with exit 124; never reached L2. |
| 112 | curl | `/usr/bin/curl --version` | PASS L2 | 11 | 2419/2419 messages. |
| 113 | wget | `/usr/bin/wget --version` | PASS L2 | 11 | 1674/1674 messages. |
| 114 | ssh | `/usr/bin/ssh -V` | PASS L2 | 11 | 1406/1406 messages. |
| 115 | gpg | `/usr/bin/gpg --version` | PASS L2 | 11 | 1569/1569 messages after isolating verification logs with `TMPDIR`. |
| 116 | vim | `/bin/sh -c '/usr/bin/vim --version \| /usr/bin/head -5'` | PASS L2 | 11 | 3511/3511 messages with isolated `TMPDIR`. |
| 117 | less | `/usr/bin/less --version` | PASS L2 | 11 | 1075/1075 messages with isolated `TMPDIR`. |
| 118 | man | `/usr/bin/man --version` | PASS L2 | 11 | 1287/1287 messages with isolated `TMPDIR`. |
| 119 | tmux | `/usr/bin/tmux -V` | PASS L2 | 11 | 895/895 messages with isolated `TMPDIR`. |

## Signals and edge cases

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 120 | kill probe | `bash -c 'kill -0 $$'` | PASS L2 | 12 | |
| 121 | SIGTERM trap | `bash -c 'trap "echo caught" SIGTERM; kill -TERM $$; echo done'` | PASS L2 | 12 | Handler prints `caught`, then `done`. |
| 122 | exit 42 | `run --strict --verify --verify-allow both -- bash -c 'exit 42'` | PASS L2 | 12 | Hermit propagates guest exit 42 after verification. |
| 123 | false | `run --strict --verify --verify-allow both -- /bin/false` | PASS L2 | 12 | Hermit propagates guest exit 1 after verification. |
| 124 | SIGPIPE pipeline | `bash -c 'yes \| head -100 >/dev/null; echo piped_ok'` | PASS L2 | 12 | |
| 125 | background wait | `bash -c 'sleep 0.01 & wait $!'` | PASS L2 | 12 | |
| 126 | repeated fork/exec | `bash -c 'for i in $(seq 1 50); do /bin/true; done; echo loop_ok'` | PASS L2 | 12 | Fifty fork/exec operations. |
| 127 | PID virtualization | `bash -c 'echo $$; echo $PPID'` | PASS L2 | 12 | PID 3 and PPID 1. |
| 128 | directory operations | `bash -c 'cd /tmp && pwd'` | PASS L2 | 12 | Guest-private `/tmp`. |
| 129 | minimal environment | `env -i PATH=/usr/bin:/bin HOME=/tmp /bin/sh -c 'echo hello'` | PASS L2 | 12 | |
| 130 | resource limit | `bash -c 'ulimit -n'` | PASS L2 | 12 | Stable value 524288. |

## Complex pipelines and shell

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 131 | Three-stage pipeline | bash -c 'cat /etc/passwd &#124; grep root &#124; cut -d: -f1' | PASS L2 | 13 | Pipe, grep, and cut all complete deterministically. |
| 132 | Sort pipeline | bash -c 'seq 1 1000 &#124; sort -n &#124; tail -5' | PASS L2 | 13 | |
| 133 | tr and rev | bash -c 'echo hello world &#124; tr a-z A-Z &#124; rev' | PASS L2 | 13 | |
| 134 | Shell loop and wc | bash -c 'for f in /etc/hostname /etc/resolv.conf; do wc -l < "$f"; done' | PASS L2 | 13 | |
| 135 | Named FIFO | bash producer/consumer over mkfifo | FAIL | 13 | Hangs in a single strict run; the peer opener cannot run while the other process blocks. |
| 136 | Meta Python pipeline | bash -c 'python3 -c "print(42)" &#124; grep 42' | FAIL | 13 | Meta Python startup diverges even without the pipe. |
| 137 | Command substitution | bash -c 'A=$(echo hello); echo "$A world"' | PASS L2 | 13 | |
| 138 | Subshell pipeline | bash -c 'echo start; (echo sub1; echo sub2) &#124; sort; echo end' | PASS L2 | 13 | |
| 139 | Process substitution | bash -c 'diff <(echo a) <(echo b); true' | PASS L2 | 13 | |
| 140 | Bash coprocess | bash coproc echo/read/kill probe | PASS L2 | 13 | Expected termination accepted with verify-allow both. |

## Networking inspection

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 141 | hostname | hostname -f | PASS L2 | 14 | Reports hermetic-container.local. |
| 142 | getent hosts | getent hosts localhost | PASS L2 | 14 | |
| 143 | getent passwd | getent passwd root | PASS L2 | 14 | |
| 144 | getent group | getent group root | PASS L2 | 14 | |
| 145 | ip link | ip link show lo | PASS L2 | 14 | Stable loopback metadata. |
| 146 | ip address | ip addr show lo | PASS L2 | 14 | |
| 147 | ss | ss -tlnp | PASS L2 | 14 | Stable empty listening-socket table. |
| 148 | netstat | netstat -tlnp | PASS L2 | 14 | Stable empty listening-socket table. |
| 149 | ipcalc | ipcalc 192.168.1.0/24 | PASS L2 | 14 | Pure computation. |
| 150 | nslookup | nslookup localhost | FAIL | 14 | Deterministic L1 in 3/3 runs, but exits 1 because networking is disabled; verify stops after run 1. |
| 151 | netcat connect | nc -z localhost 1 | FAIL | 14 | Deterministic L1 in 3/3 runs with expected refused-connect exit 1; not L2-coverable under default policy. |

## Multi-threaded stress results

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 152 | C matrix multiply | 100x100 matrix workload | PASS L2 | 15 | Stable 5/5; checksum 29995802. |
| 153 | C Fibonacci | recursive fib(30) | PASS L2 | 15 | Stable 5/5; result 832040. |
| 154 | Rust parallel sort | four-thread sort and merge | PASS L2 | 15 | Stable 5/5; 10,000 elements. |
| 155 | Go concurrent word count | four-goroutine map/reduce | PASS L2 | 15 | Stable 5/5. |
| 156 | Meta Python Fibonacci | python3 recursive fib(30) | FAIL | 15 | Flaky L2: 1/5 pass; interpreter startup race. |
| 157 | Meta Python sqlite3 | python3 in-memory SQLite aggregate | FAIL | 15 | Flaky L2: 2/5 pass; interpreter startup race. |
| 158 | Meta Python word count | python3 dictionary and sort workload | FAIL | 15 | Flaky L2: 2/5 pass; interpreter startup race. |
| 159 | C SQLite | compile SQLite C workload | NOT RUN | 15 | sqlite3.h development headers were unavailable. |

## Compilation pipeline

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 160 | gcc compile util | gcc -c util.c -o /tmp/util.o | PASS L2 | 16 | Output is isolated per verify run. |
| 161 | gcc compile main | gcc -c main.c -o /tmp/main.o | PASS L2 | 16 | Output is isolated per verify run. |
| 162 | gcc link | gcc pre-staged objects -o /tmp/myapp | PASS L2 | 16 | Inputs begin identical in both runs. |
| 163 | make full build | make out-of-tree with O=/tmp/b16 | PASS L2 | 16 | Complete multi-process build in one invocation. |
| 164 | make no-op | make with targets already current | PASS L2 | 16 | Stable input filesystem. |
| 165 | Compiled C app | /tmp/myapp | PASS L2 | 16 | Prints add=5 mul=20. |
| 166 | CMake | cmake --version | PASS L2 | 16 | |
| 167 | gcc syntax check | gcc -fsyntax-only | PASS L2 | 16 | Writes no output. |
| 168 | gcc overwrite control | gcc -c with pre-created output | PASS L2 | 16 | Both runs observe the same initial output state. |
| 169 | gcc persistent util output | gcc -c util.c -o PROJ/util.o | FAIL | 16 | Run 2 observes the file created by run 1. |
| 170 | gcc persistent main output | gcc -c main.c -o PROJ/main.o | FAIL | 16 | Persistent filesystem state differs. |
| 171 | gcc persistent link output | gcc objects -o PROJ/myapp | FAIL | 16 | Persistent filesystem state differs. |
| 172 | make in-tree build | make in persistent project tree | FAIL | 16 | Run 1 builds; run 2 sees up-to-date targets. |

## Language test frameworks

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 173 | Rust Cargo tests | copy project to isolated /tmp; CARGO_NET_OFFLINE=true cargo test | PASS L2 | 17 | Three unit tests pass in each run with 18,769 matching deterministic messages. |
| 174 | Meta Python unittest | copy project to isolated /tmp; python3 -m unittest test_file.py | FAIL | 17 | All five tests run, but clone/futex scheduling diverges. |
| 175 | Go tests | GOMAXPROCS=1 GOFLAGS='-count=1 -p=1' go test ./... | FAIL | 17 | Run 1 stalls around clone/vfork and nanosleep; no L1 result. |

## Larger compilation projects

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 176 | Five-file C make | make -j1 with O=/tmp/cbld1 | PASS L2 | 18 | Out-of-tree output. |
| 177 | Five-file manual gcc | compile five objects and link in one invocation | PASS L2 | 18 | All outputs remain in isolated /tmp. |
| 178 | Optimized rustc | rustc -O -o /tmp/rust-app | PASS L2 | 18 | Includes linker child process. |
| 179 | GNU as and ld | assemble, link, and run freestanding binary in /tmp | PASS L2 | 18 | |
| 180 | Go build | GOFLAGS=-p=1 go build -o /tmp/goapp | FAIL | 18 | Intermittent L2: 7/8 pass, one scheduling divergence. |
| 181 | Parallel make | make -j2 with O=/tmp/cbld2 | FAIL | 18 | Hangs even in a single run; make jobserver pipe rendezvous deadlocks. |

## Containers and system identity

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 182 | unshare | unshare --pid --fork -- /bin/echo hello | PASS L2 | 19 | |
| 183 | chroot | chroot --version | PASS L2 | 19 | |
| 184 | nsenter | nsenter --version | PASS L2 | 19 | |
| 185 | getconf page size | getconf PAGE_SIZE | PASS L2 | 19 | |
| 186 | getconf CPU count | getconf _NPROCESSORS_ONLN | PASS L2 | 19 | |
| 187 | getconf word size | getconf LONG_BIT | PASS L2 | 19 | |
| 188 | arch | arch | PASS L2 | 19 | |
| 189 | nproc | nproc | PASS L2 | 19 | |
| 190 | lscpu | lscpu | PASS L2 | 19 | |
| 191 | taskset | taskset -p 1 | PASS L2 | 19 | |
| 192 | capsh | capsh --print | FAIL | 19 | NSCD socket request count differs between runs. |

## Crypto and randomness

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 193 | OpenSSL random | openssl rand -hex 16 | PASS L2 | 20 | |
| 194 | OpenSSL encryption | openssl enc AES-256-CBC with PBKDF2 over /etc/hostname | PASS L2 | 20 | |
| 195 | OpenSSL digest | openssl dgst -sha256 /etc/hostname | PASS L2 | 20 | |
| 196 | UUID | uuidgen | PASS L2 | 20 | |
| 197 | shuf | shuf deterministic fixture | PASS L2 | 20 | Random choice is virtualized. |
| 198 | Random sort | sort -R fixture &#124; head | PASS L2 | 20 | |
| 199 | mktemp | mktemp | PASS L2 | 20 | |
| 200 | urandom | dd from /dev/urandom &#124; xxd | PASS L2 | 20 | |
| 201 | GPG random | gpg --batch --gen-random 0 16 &#124; xxd | FAIL | 20 | Run 1 creates ~/.gnupg/random_seed, so run 2 starts from different filesystem state. |
| 202 | GPG isolated-home control | GNUPGHOME=/tmp/gpg-batch20 gpg --batch --gen-random 0 16 &#124; xxd | PASS L2 | 20 | Per-run isolated seed state proves the RNG path deterministic. |

## Scripting languages

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 203 | Perl hello | perl -e print hello | PASS L2 | 21 | |
| 204 | Perl sum | perl sum 1 through 100 | PASS L2 | 21 | |
| 205 | Perl time formatting | perl POSIX strftime with gmtime(0) | PASS L2 | 21 | |
| 206 | Awk sum | awk BEGIN loop summing 1 through 100 | PASS L2 | 21 | |
| 207 | Awk file rows | awk print NR and line for /etc/hostname | PASS L2 | 21 | |
| 208 | bc arithmetic | echo 1+2+3+4+5 &#124; bc | PASS L2 | 21 | |
| 209 | bc math library | echo scale=10; 4*a(1) &#124; bc -l | PASS L2 | 21 | |
| 210 | Lua | lua -e print hello | PASS L2 | 21 | Lua 5.4.4. |
| 211 | Ruby | ruby -e puts 1+2 | FAIL | 21 | Host Ruby installation lacks RubyGems/RbConfig and fails identically natively. |
| 212 | Ruby without gems | ruby --disable-gems -e puts 1+2 | PASS L2 | 21 | Host-packaging workaround. |
| 213 | Node.js | node -e console.log(42) | PASS L2 | 21 | |

## JVM compilation and execution

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 214 | Java version | java -version | PASS L2 | 22 | OpenJDK 1.8.0_492. |
| 215 | Java compiler | javac Hello.java | FAIL | 22 | Run 2 observes Hello.class created by run 1. |
| 216 | Java class | java Hello | PASS L2 | 22 | Fixture in a guest-visible worktree path. |
| 217 | Java JAR | java -jar Hello.jar | PASS L2 | 22 | |
| 218 | JShell | jshell --version | PASS L2 | 22 | JShell 17.0.18. |
| 219 | Java HashMap | java HashMapOrder | PASS L2 | 22 | Stable iteration output. |

## Structured data processing

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 220 | jq | echo JSON &#124; jq .a | PASS L2 | 23 | |
| 221 | xmllint | echo XML &#124; xmllint --xpath //a/text() - | PASS L2 | 23 | |
| 222 | sqlite3 | sqlite3 :memory: create, insert, and sum | PASS L2 | 23 | |
| 223 | sed and head | sed substitution over /etc/passwd &#124; head -3 | PASS L2 | 23 | |
| 224 | comm | comm over two process substitutions | PASS L2 | 23 | |
| 225 | join | join over two process substitutions | PASS L2 | 23 | |
| 226 | Meta Python JSON | python3 JSON load/dump | FAIL | 23 | Default Meta runtime diverges after clone3; stock /usr/bin/python3 control passes L2. |
| 227 | Meta Python CSV | python3 CSV reader | FAIL | 23 | Same Meta runtime scheduling divergence; stock Python control passes L2. |
| 228 | paste process substitutions | paste -d, <(seq 1 5) <(seq 6 10) | FAIL | 23 | Run 1 hangs reading one producer while the other writer remains pending. |

## Archive and packaging

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 229 | gzip | gzip to /tmp and gunzip to stdout | PASS L2 | 24 | Round trip. |
| 230 | bzip2 | bzip2 to /tmp and decompress to stdout | PASS L2 | 24 | Round trip. |
| 231 | xz | xz to /tmp and decompress to stdout | PASS L2 | 24 | Round trip. |
| 232 | zip | zip to /tmp and unzip to stdout | PASS L2 | 24 | |
| 233 | cpio | create archive in /tmp and list it | PASS L2 | 24 | |
| 234 | ar | ar rcs /tmp/out.a and ar t | PASS L2 | 24 | |
| 235 | tar | tar cf /tmp/out.tar and tar tf | PASS L2 | 24 | Also passed repeated, multi-file extraction, and tar+gzip controls. |

## System administration and binary inspection

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 236 | strace | `strace -c /bin/true` | FAIL | 25 | Nested `PTRACE_TRACEME` is denied under Hermit's ptrace backend; run 1 exits 1. |
| 237 | ltrace | `ltrace --version` | NOT RUN | 25 | Utility was not installed. |
| 238 | ldd | `ldd /bin/ls` | PASS L2 | 25 | |
| 239 | file | `file /bin/ls` | PASS L2 | 25 | |
| 240 | readelf | `readelf -h /bin/ls` | PASS L2 | 25 | |
| 241 | objdump | `objdump -f /bin/ls` | PASS L2 | 25 | |
| 242 | nm | `nm target/release/hermit 2>/dev/null &#124; head -20` | PASS L2 | 25 | The requested `libc.a` was absent; the readable Hermit binary was the recorded substitute. |
| 243 | strings | `strings /bin/ls &#124; head -20` | PASS L2 | 25 | |
| 244 | size | `size /bin/ls` | PASS L2 | 25 | |
| 245 | hexdump | `hexdump -C /bin/ls &#124; head -10` | PASS L2 | 25 | |
| 246 | od | `od -A x -t x1z /bin/ls &#124; head -10` | PASS L2 | 25 | |

## Editors and text output

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 247 | ed | `ed -s` with scripted append, print, and quit input | PASS L2 | 26 | |
| 248 | ex | `ex -s +%p +q! /etc/hostname` | PASS L2 | 26 | |
| 249 | fmt | `fmt -w 40 /etc/passwd &#124; head -5` | PASS L2 | 26 | |
| 250 | fold | `fold -w 40 /etc/passwd &#124; head -5` | PASS L2 | 26 | |
| 251 | column | `column -t /etc/passwd -s: &#124; head -5` | PASS L2 | 26 | |
| 252 | pr | `pr -l 20 /etc/hostname` | PASS L2 | 26 | |
| 253 | expand | `expand /etc/hostname` | PASS L2 | 26 | |
| 254 | unexpand | `unexpand /etc/hostname` | PASS L2 | 26 | |
| 255 | rev | `rev /etc/hostname` | PASS L2 | 26 | |
| 256 | tac | `tac /etc/hostname` | PASS L2 | 26 | |

## Additional math and numeric tools

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 257 | dc | `dc -e '2 3 + p'` | PASS L2 | 27 | |
| 258 | factor | `factor 12345` | PASS L2 | 27 | |
| 259 | numfmt | `numfmt --to=iec 1048576` | PASS L2 | 27 | |
| 260 | printf hexadecimal | `printf '%x\n' 255` | PASS L2 | 27 | |
| 261 | printf floating point | `printf '%.10f\n' 3.14159265358979` | PASS L2 | 27 | |
| 262 | expr | `expr 7 '*' 8` | PASS L2 | 27 | |
| 263 | seq | `seq -f '%.3f' 0 0.1 1.0` | PASS L2 | 27 | |
| 264 | Meta Python factorial | `python3 -c 'import math; print(math.factorial(20))'` | FAIL | 27 | Both runs finish, but threaded startup RNG-seed and scheduling order diverge before the calculation. |
| 265 | Meta Python hashlib | `python3 -c 'import hashlib; print(hashlib.sha256(b"hello").hexdigest())'` | FAIL | 27 | Same Meta Python startup divergence. |
| 266 | OpenSSL speed | `openssl speed -elapsed -seconds 1 sha256 2>&1 &#124; tail -3` | FAIL | 27 | Scheduler logs match, but measured throughput differs between the two runs; the full verify takes about 147 seconds. |

## Filesystem, identity, and permissions

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 267 | stat | `stat /etc/hostname` | FAIL | 28 | Flaky at L2: 1/3 passed; two runs diverged when external nscd poll readiness selected different NSS paths. |
| 268 | stat filesystem | `stat -f /etc/hostname` | PASS L2 | 28 | |
| 269 | touch and stat | `bash -c 'touch /tmp/hermit-test-file && stat /tmp/hermit-test-file'` | PASS L2 | 28 | Guest-private `/tmp` gives each run fresh state. |
| 270 | df | `df /tmp` | PASS L2 | 28 | |
| 271 | du | `du -sh /etc/hostname` | PASS L2 | 28 | |
| 272 | find | `find /etc -maxdepth 1 -name 'host*' -type f` | PASS L2 | 28 | |
| 273 | ls file | `ls -la /etc/hostname` | PASS L2 | 28 | |
| 274 | recursive ls | create a fixed `/tmp/hermit-test` tree, then `ls -laR` | PASS L2 | 28 | Fixture is created inside each isolated guest run. |
| 275 | realpath | `realpath /etc/hostname` | PASS L2 | 28 | |
| 276 | basename | `basename /etc/hostname` | PASS L2 | 28 | |
| 277 | dirname | `dirname /etc/hostname` | PASS L2 | 28 | |
| 278 | id | `id` | FAIL | 30 | Stateful AF_UNIX NSS/nscd traffic differs between runs. |
| 279 | whoami | `whoami` | PASS L2 | 30 | |
| 280 | groups | `groups` | FAIL | 30 | Run 1 exits nonzero because GID 65534 has no resolvable group name. |
| 281 | logname | `logname` | PASS L2 | 30 | |
| 282 | printenv | `printenv HOME` | PASS L2 | 30 | |
| 283 | env | `env &#124; sort &#124; head -10` | PASS L2 | 30 | |
| 284 | umask | `bash -c 'umask'` | PASS L2 | 30 | |
| 285 | chmod | `touch /tmp/hermit-perms; chmod 755; stat -c %a` | PASS L2 | 30 | |
| 286 | install | `install -m 644 /etc/hostname /tmp/hermit-install-test` | PASS L2 | 30 | |
| 287 | mkfifo | `bash -c 'mkfifo /tmp/hermit-fifo-test && echo ok'` | PASS L2 | 30 | Creates a FIFO without opening both ends. |
| 288 | file test | `bash -c 'test -f /etc/hostname && echo exists'` | PASS L2 | 30 | |

## Network clients and web operations

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 289 | curl version | `curl --version` | PASS L2 | 29 | |
| 290 | wget version | `wget --version` | PASS L2 | 29 | |
| 291 | curl localhost | curl `localhost:1` with status capture | PASS L2 | 29 | Deterministic refused-connect path. |
| 292 | wget localhost | wget spider `localhost:1` with status capture | PASS L2 | 29 | Deterministic refused-connect path. |
| 293 | ping loopback | `ping -c 1 -w 1 127.0.0.1` | PASS L2 | 29 | |
| 294 | dig localhost | `dig +short localhost @127.0.0.1` with status capture | PASS L2 | 29 | |
| 295 | host localhost | `host localhost` with status capture | PASS L2 | 29 | |
| 296 | whois fallback | `whois --version 2>/dev/null || echo 'not installed'` | PASS L2 | 29 | `whois` was absent; this verifies only the fallback shell path. |
| 297 | ftp/lftp fallback | `ftp --version || lftp --version; echo $?` | PASS L2 | 29 | Both clients were absent; this verifies only the fallback/status path. |
| 298 | rsync version | `rsync --version` | PASS L2 | 29 | |
| 299 | scp invalid version option | `scp -V` | FAIL | 29 | This OpenSSH scp rejects `-V` and exits 1 before run 2; a status-capturing control is deterministic. |
| 300 | curl external GET | curl status for `https://example.com` | FAIL | 34 | Run 1 exits 7 after an IPv6 connect returns `ENETUNREACH`; external network success is outside scope. |
| 301 | wget external GET | `wget -q -O /tmp/example.html https://example.com` | FAIL | 34 | Run 1 exits 4 after `ENETUNREACH`; external network success is outside scope. |
| 302 | OpenSSL external TLS | `openssl s_client -connect example.com:443 &#124; head -5` | PASS L2 | 34 | Deterministic DNS failure path; pipeline exits 0 because `head` succeeds, not because TLS connected. |
| 303 | curl localhost POST | curl POST to `localhost:1` with status capture | PASS L2 | 34 | Deterministic refused-connect exit 7. |
| 304 | Meta Python urllib | urllib request to `localhost:1` with status capture | FAIL | 34 | Both runs finish, but Meta Python startup scheduling and RNG seed order diverge. |
| 305 | wget localhost headers | wget `localhost:1` with status capture | PASS L2 | 34 | Deterministic refused-connect exit 4. |

## Time and process control

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 306 | date current | `date +%Y-%m-%d` | PASS L2 | 31 | Virtual date is 2021-12-31. |
| 307 | date epoch | `date -d @0 +%Y-%m-%d` | PASS L2 | 31 | Local timezone produced 1969-12-31. |
| 308 | date to epoch | `date -d '2024-01-01' +%s` | PASS L2 | 31 | |
| 309 | cal January | `cal 1 2024` | PASS L2 | 31 | |
| 310 | cal December | `cal 12 2025` | PASS L2 | 31 | |
| 311 | ncal | `ncal 2024` | NOT RUN | 31 | Utility was not installed. |
| 312 | Bash SECONDS | `bash -c 'SECONDS=0; sleep 0; echo $SECONDS'` | PASS L2 | 31 | Output 0. |
| 313 | timeout status | `timeout 1 sleep 10; echo $?` in a guest shell | PASS L2 | 31 | Deterministic captured status 124. |
| 314 | Bash time | `bash -c 'time echo hello' 2>&1` | PASS L2 | 31 | Deterministic virtual timing output. |
| 315 | nice | `nice -n 5 echo hello` | PASS L2 | 32 | |
| 316 | background wait | `bash -c 'sleep 0.01 & wait $! && echo waited'` | PASS L2 | 32 | |
| 317 | nohup | `bash -c 'nohup echo hello 2>/dev/null && echo ok'` | PASS L2 | 32 | |
| 318 | short timeout | `timeout 2 sleep 0.01 && echo 'timeout ok'` | FAIL | 32 | Under Hermit the child is still loading when virtual timeout expires, so it exits 124; native exits 0. A status-capturing control passes L2. |
| 319 | minimal environment | `env -i HOME=/tmp PATH=/usr/bin echo hello` | PASS L2 | 32 | |
| 320 | signal probe | Bash trap plus `kill -0 $$` | PASS L2 | 32 | |
| 321 | yes pipeline | `yes &#124; head -100 &#124; wc -l` | PASS L2 | 32 | Output 100. |
| 322 | xargs | `xargs echo < /etc/hostname` | PASS L2 | 32 | |
| 323 | Bash coprocess | `coproc cat` round trip | PASS L2 | 32 | |

## C++ programs

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 324 | C++ hello | `g++ -O2`, then run `cpp_hello` | PASS L2 | 33 | Used `--tmp=/tmp` to expose the host-built binary; no determinism relaxation. |
| 325 | C++ STL | vector, map, set, and unordered_map fixture | PASS L2 | 33 | |
| 326 | C++ templates | compile-time Fibonacci fixture | PASS L2 | 33 | |
| 327 | C++ exceptions | throw/catch and RTTI fixture | PASS L2 | 33 | |
| 328 | C++ threads | two threads, mutex, and condition variable | PASS L2 | 33 | |

## Parallel execution and Hermit fixtures

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 329 | sequential xargs | `seq 1 10 &#124; xargs -n1 echo` | PASS L2 | 35 | |
| 330 | xargs one worker | `seq 1 5 &#124; xargs -P1 -I{} echo 'item {}'` | PASS L2 | 35 | |
| 331 | xargs two workers | `seq 1 5 &#124; xargs -P2 -I{} echo 'item {}'` | PASS L2 | 35 | |
| 332 | Bash background jobs | three background `echo` jobs and `wait` | PASS L2 | 35 | |
| 333 | Bash arithmetic | sum 1 through 100 | PASS L2 | 35 | Output 5050. |
| 334 | named FIFO rendezvous | `mkfifo; echo hello > pipe & cat pipe; wait` | FAIL | 35 | Run 1 hangs: writer blocks in `openat(O_WRONLY)` while serialization prevents the reader from reaching its matching open. |
| 335 | Bash `/dev/tcp` | connect to `localhost:1` with failure capture | PASS L2 | 35 | Deterministic connection-refused path. |
| 336 | Bash timed read | `read -t 0.001` from `/dev/null` | PASS L2 | 35 | Captured status output is deterministic. |
| 337 | clock_gettime fixture | `rustbin_clock_gettime` | PASS L2 | 36 | Repository standalone fixture. |
| 338 | exit_group fixture | `exit_group` | PASS L2 | 36 | Repository standalone fixture. |
| 339 | pipe_basics fixture | `pipe_basics` | PASS L2 | 36 | Repository standalone fixture. |
| 340 | poll fixture | `poll` | PASS L2 | 36 | Repository standalone fixture. |
| 341 | socketpair fixture | `socketpair` | PASS L2 | 36 | Repository standalone fixture. |
| 342 | futex fixture | `futex_and_print` | PASS L2 | 36 | Repository standalone fixture. |
| 343 | stack pointer fixture | `stack_ptr` | PASS L2 | 36 | Repository standalone fixture. |
| 344 | thread random fixture | `thread_random` | PASS L2 | 36 | Repository standalone fixture. |

Batch 36 infrastructure checks are not counted as strict program outcomes:
`cargo test -p detcore-model --lib` passed 17 tests; `cargo test -p hermit-verify`
passed 25 tests. The requested `cargo test -p hermit-verify --lib` is not a
valid target because that package has no library target.

## Larger real applications

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 345 | sqlite3 larger query | create, insert, select, count, and sum in `:memory:` | PASS L2 | 37 | Output rows 1 through 3, count 3, and sum 6. |
| 346 | Meta Python JSON | build and pretty-print nested user JSON | FAIL | 37 | L1 output is correct, but the two L2 runs schedule launcher children differently at COMMIT turn 29; stock Python 3.9 control passes. |
| 347 | Meta Python CSV | write a two-row CSV in memory | FAIL | 37 | Same Meta launcher/runtime scheduling divergence; stock Python 3.9 control passes. |
| 348 | Node.js JSON | print `{a:1,b:[2,3]}` as JSON | PASS L2 | 37 | |
| 349 | Node.js array | print squares from 0 through 9 | PASS L2 | 37 | Output 0 through 81. |
| 350 | Lua loop | print integers 1 through 10 | PASS L2 | 37 | |
| 351 | Perl Digest::MD5 | print MD5 of `hello` | PASS L2 | 37 | Output `5d41402abc4b2a76b9719d911017c592`. |

## Additional data processing and system information

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 352 | sqlite3 sums | create two three-column rows and select `a+b+c` | PASS L2 | 38 | Outputs 6 and 15. |
| 353 | Meta Python hashlib | SHA-256 of `hermit` | FAIL | 38 | Logical commit time differs by 20ns after startup RNG/gettimeofday ordering swaps; stock Python 3.9 control passes L2. |
| 354 | Perl POSIX date | POSIX `strftime` of local epoch time | PASS L2 | 38 | Output 1969-12-31 in the local timezone. |
| 355 | awk users | `awk -F: '{print $1}' /etc/passwd &#124; head -5` | PASS L2 | 38 | |
| 356 | sed lines | `sed -n '1,5p' /etc/passwd` | PASS L2 | 38 | |
| 357 | cut and sort | `cut -d: -f1 /etc/passwd &#124; sort &#124; head -5` | PASS L2 | 38 | |
| 358 | tr hostname | `tr 'a-z' 'A-Z' < /etc/hostname` | PASS L2 | 38 | |
| 359 | paste process substitutions | `paste -d, <(seq 1 3) <(seq 4 6)` | FAIL | 38 | Run 1 hangs after one writer completes while the second writer and paste reader remain blocked. |
| 360 | nl hostname | `nl /etc/hostname` | PASS L2 | 38 | |
| 361 | lscpu | `lscpu` | FAIL | 39 | CPU scaling MHz changes from 74% to 72% between runs. |
| 362 | free | `free -m` | FAIL | 39 | Used, free, and available host memory change by 31 MiB. |
| 363 | df | `df -h` | PASS L2 | 39 | |
| 364 | mount | `mount` | PASS L2 | 39 | |
| 365 | lsblk | `lsblk` | PASS L2 | 39 | |
| 366 | ps | `ps aux` | FAIL | 39 | Hermit VSZ and RSS values differ between runs. |
| 367 | uptime | `uptime` | PASS L2 | 39 | |
| 368 | uname | `uname -a` | PASS L2 | 39 | |
| 369 | getconf | `getconf PAGESIZE` | PASS L2 | 39 | |

## Signals, IPC, and math/science

| # | Program | Command | Result | Batch | Notes |
|---:|---|---|---|---:|---|
| 370 | SIGTERM trap | `bash -c 'trap "echo caught" SIGTERM; kill -TERM $$; echo after'` | PASS L2 | 40 | Output is `caught`, then `after`. |
| 371 | SIGALRM trap | `bash -c 'trap "echo alarm" SIGALRM; kill -ALRM $$'` | PASS L2 | 40 | Output is `alarm`. |
| 372 | ignored SIGINT | `bash -c 'trap "" SIGINT; echo immune'` | PASS L2 | 40 | |
| 373 | signal existence probe | `bash -c 'kill -0 $$; echo alive=$?'` | PASS L2 | 40 | Output is `alive=0`. |
| 374 | set -e recovery | `bash -c 'set -e; false &#124;&#124; echo recovered'` | PASS L2 | 40 | |
| 375 | subshell exit status | `bash -c '(exit 42); echo parent=$?'` | PASS L2 | 40 | Output is `parent=42`. |
| 376 | timeout status | `bash -c 'timeout 1 sleep 10; echo timeout=$?'` | PASS L2 | 40 | Output is `timeout=124`; L2 took 2.20 seconds wall time. |
| 377 | EXIT trap | `bash -c 'trap "echo EXIT" EXIT; echo main'` | PASS L2 | 40 | Output is `main`, then `EXIT`. |
| 378 | fd 3 redirection | `bash -c 'exec 3>&1; echo redirected >&3'` | PASS L2 | 40 | |
| 379 | simple pipe | `bash -c 'echo hello &#124; cat'` | PASS L2 | 41 | 1633/1633 DETLOG and scheduler COMMIT messages. |
| 380 | named FIFO rendezvous | `bash -c 'mkfifo /tmp/fifo41; echo test > /tmp/fifo41 & cat /tmp/fifo41; wait; rm /tmp/fifo41'` | FAIL | 41 | Run 1 hangs: the writer blocks opening the FIFO while the reader is not scheduled. |
| 381 | System V IPC listing | `ipcs` | PASS L2 | 41 | |
| 382 | Bash coprocess | Bash `coproc` round trip through `cat` | PASS L2 | 41 | 1965/1965 DETLOG and scheduler COMMIT messages. |
| 383 | process substitution | `bash -c 'read -r line < <(echo subprocess); echo $line'` | FAIL | 41 | Run 1 hangs: the parent blocks reading while the child writer cannot run. |
| 384 | short sleep | `bash -c 'echo start; sleep 0.001; echo end'` | PASS L2 | 41 | |
| 385 | read/write fd | `bash -c 'exec 3<>/tmp/ipc41; echo hello >&3; cat <&3; rm /tmp/ipc41'` | PASS L2 | 41 | |
| 386 | bc pi | `sh -c "echo 'scale=20; 4*a(1)' &#124; bc -l"` | PASS L2 | 42 | |
| 387 | dc pi | `dc -e '10 k 355 113 / p'` | PASS L2 | 42 | |
| 388 | Meta Python math | `python3 -c 'import math; print(math.pi, math.e, math.factorial(20))'` | PASS L2 | 42 | |
| 389 | Meta Python sum | `python3 -c 'print(sum(range(1000)))'` | FAIL | 42 | Helper-thread startup diverges after clone3: parent futex progress and child RNG initialization occur in different orders. |
| 390 | awk sum | `awk 'BEGIN{for(i=1;i<=100;i++) s+=i; print s}'` | PASS L2 | 42 | Output is 5050. |
| 391 | Perl POSIX math | `perl -e 'use POSIX; print POSIX::ceil(3.14), " ", POSIX::floor(3.14), "\n"'` | PASS L2 | 42 | |
| 392 | seq and awk sum | `sh -c "seq 1 1000 &#124; awk '{s+=$1} END{print s}'"` | PASS L2 | 42 | |
| 393 | bc integer power | `sh -c "echo '2^64' &#124; bc"` | PASS L2 | 42 | |
| 394 | Meta Python seeded RNG | `python3 -c 'import random; random.seed(42); print([random.randint(0,100) for _ in range(10)])'` | FAIL | 42 | Explicit application seeding does not remove the Meta runtime's helper-thread startup scheduling divergence. |

## Record/replay results

Record/replay uses `hermit record start --verify -- PROGRAM` on the ptrace
backend with default logging and no relaxations. These outcomes are a separate
assurance dimension from strict L2 and are excluded from the strict summary.

| Evidence set | PASS | FAIL | Result and limitation |
|---|---:|---:|---|
| Initial 20-program single-process matrix, debug binary | 18 | 2 | `echo`, `cat`, `wc`, `sort`, `head`, `uniq`, `tr`, `cut`, `paste`, `seq`, `date`, `hostname`, `arch`, `nproc`, `getconf`, `bc`, `awk`, and `perl` matched recording. `tail` failed during recording/event replay, and `yes &#124; head` timed out during recording. |
| Expansion batch 2, pre-fix release binary | 0 | 22 | 21 programs recorded successfully but replay's initial `execve` received corrupt envp and returned `EFAULT`; `hostname -f` separately panicked during recorder netlink handling. |
| Expansion batch 3, pre-fix release binary | 0 | 8 | Every compiler/computation recording completed; every replay failed its first `execve` with the same corrupt envp. |
| Expansion batch 4, pre-fix release binary | 0 | 9 | All C/C++/Rust guests recorded and exited 0; all replays failed initial `execve` with corrupt envp. |
| Expansion batch 5, pre-fix release binary | 0 | 10 | Eight replays failed initial `execve`; Node recording hit an unsupported ioctl and javac recording did not complete. |
| Focused validation after envp fix | 4 | 0 | At PR #238 head, `/bin/echo` matched 3/3 and `java -version` matched 1/1 on a host with `/usr/local/fbcode`. A separate validation also matched `getent hosts localhost`. |
| Post-envp general retest on main | 16 | 4 | Echo, cat, wc, getent, cal, arch, nproc, Perl, awk, Lua, dc, sqlite3, direct gzip/gunzip, factor, and printf match. `hostname -f` hits the recorder NULL-optval panic; date and two gcc cases diverge in replay fd ordering. |
| Post-envp scripting retest on main | 11 | 1 | Lua, Ruby, Perl, awk, bc, dc, factor, sqlite3, jq, Java, and OpenSSL match. Node recording still panics on unsupported ioctl request 35142 and never reaches replay. |
| Post-envp compilation and multi-process retest | 4 | 6 | Make, a Bash builtin loop, `sh` cat/echo, and `wc` match. gcc and g++ replay diverge before executing their generated binaries; rustc recording does not complete; three shell pipelines diverge through stdout routing, fd allocation, or SIGCHLD ordering. |
| Post-envp Hermit fixtures and shell-form retest | 4 | 4 | All four repository fixtures match. The four prescribed shell forms fail through duplicated pipeline output, process-substitution fd ordering, or process-substitution liveness. Six direct-utility isolation controls also match but are excluded from this prescribed-case count. |
| Post-envp data-processing retest | 6 | 4 | Direct sed, tr, nl, wc, tee, and rev match. Pipelines using cut, sort, uniq, or xargs expose duplicated intermediate output, fd allocation, or SIGCHLD ordering failures. |
| Post-envp system-information retest | 12 | 0 | uname, arch, nproc, getconf, uptime, df, mount, lsblk, id, whoami, logname, and printenv all match. |
| Post-envp signal and Bash retest | 7 | 3 | Signal traps, status handling, timeout, and a builtin loop match. Command substitution and two pipelines fail through intermediate-output leakage or replay fd allocation. Two direct isolation controls match and are excluded from this prescribed-case count. |
| **Post-envp batches 1-7 subtotal** | **60** | **22** | Counts the 82 prescribed cases in the seven numbered post-fix matrices; supplemental controls are described but not double-counted. |

PR [#238](https://github.com/rrnewton/hermit/pull/238) merged to `main` as
`46836669bd6c2f7151fbe65c55f4ea5bd1440897`. It pre-creates the fbcode bind
mount target in the parent and removes the replay child's stack-overflowing
`touch_target()` path. The program-independent replay `execve`/envp failure is
therefore fixed on current main. The historical 49 pre-fix outcomes remain
reported as measured; overlapping post-fix successes are recorded in the new
retest rows rather than retroactively relabeling those runs. `hostname -f`'s
recorder netlink panic, Node's unsupported ioctl, javac recording performance,
`tail`, concurrent pipeline liveness, intermediate-output leakage, and replay
fd allocation remain distinct observations.

PR [#239](https://github.com/rrnewton/hermit/pull/239) remains open at
`929ba884d907f86463d27ebbd3d3287df6aa49c3`. It passed compiler-focused L2
checks, but adversarial review found failed-vfork deadlock and remaining
parent/child/peer ordering races, so it was not landed.

## Failure root causes and fix status

| Failure | Root cause | Fix or workaround recorded |
|---|---|---|
| Batch 1 default tar extract | Guest euid 0 changes tar to same-owner behavior; archived host uid/gid is unmapped in the user namespace. | Yes: `--no-same-owner` passes L2. |
| Batch 1 differing-file diff | The command deterministically exits 1, but default verification accepts only successful guest exits. | Yes: `--verify-allow both` passes L2. |
| Batch 3 gcc and rustc | A parent-versus-fork/vfork-child scheduling race changes child start and RNG seed order. | No complete fix in the batch result; PR #221 was only a partial step. |
| Batches 4 and 5 Meta Python | Meta runtime startup exposes live procfs memory data and adds runtime threads whose virtual-time/RNG ordering diverges. | Workaround: stock `/usr/bin/python3.9` passes the same probes at L2; no product fix was established by these batches. |
| Batch 6 Ruby | Broken host RubyGems packaging; the same command fails natively. | Workaround: `--disable-gems` passes L2; this is not a Hermit defect. |
| Batch 7 default tar create | Owner-name resolution enters stateful NSS/nscd AF_UNIX polling; poll readiness differs. | Yes: `--numeric-owner` passes L2. |
| Batch 10 ps and free | Live procfs memory counters are not virtualized. | No fix recorded in the batch. |
| Batch 10 id | Stateful NSS/nscd cache behavior differs between verification runs. | No fix recorded in the batch. |
| Batch 10 groups | Virtual gid 65534 has no host NSS name; the first guest exits 1. | Host/NSS data fix or a numeric-output probe is required. |
| Batch 10 df | Disconnected host `/mnt/xarfuse` endpoints; native `df` also fails. | Repair or exclude the host mount; not a Hermit determinism defect. |
| Batch 10 lsof | Run 1 was killed with exit 137; no OOM or journal evidence established why. | Unknown; no fix can be claimed. |
| Batches 10 and 25 strace | Nested ptrace is unsupported under Hermit's ptrace backend. | No ptrace-backend fix recorded; use a non-nested tracing approach. |
| Batch 10 timeout | Host SIGALRM uses wall time while guest sleep advances on virtual time. | No fix recorded in the batch. |
| Batch 11 Git | Git startup spins in `sched_yield` under strict sequentialization, generates multi-gigabyte logs, and never completes run 1. | No fix validated by batch 11. |
| Batch 13 named FIFO | Blocking FIFO open/read waits for a peer process that strict serialization does not schedule. | No fix recorded; requires scheduler awareness of cross-process FIFO rendezvous. |
| Batch 13 Meta Python pipeline | The Meta Python runtime diverges during threaded startup even without a pipe. | Stock CPython is the established control; the pipeline itself is not the failure. |
| Batch 14 nslookup and nc | Both commands deterministically exit nonzero under network isolation, so default verify does not start run 2. | Use an expected-exit verification policy when the nonzero result is the intended contract. |
| Batches 15, 17, 23, 27, 34, 37, and 38 Meta Python | Clone/futex and RNG-seed ordering diverges in the multithreaded Meta runtime startup. | Stock /usr/bin/python3 controls pass; no Meta-runtime scheduling fix was established. |
| Batches 16 and 22 compiler outputs | Verify runs share persistent visible filesystem state; run 2 observes artifacts created by run 1. | Put outputs in guest-isolated /tmp, pre-stage identical outputs, or copy the project into per-run /tmp. |
| Batch 17 Go tests | Go test stalls around clone/vfork, futex, and nanosleep before producing an L1 result. | No fix recorded; single-P and warm-cache controls still stalled. |
| Batch 18 parallel make | GNU make jobserver pipe rendezvous deadlocks under serialized scheduling. | Use -j1; scheduler support for cross-process blocking pipes is required for -jN. |
| Batch 18 Go build | Residual runtime/subprocess scheduling divergence appeared once in eight L2 attempts. | Treat as intermittent, not a reliable L2 pass. |
| Batch 19 capsh | The number of NSCD AF_UNIX exchanges differs between runs. | Isolate or disable the external NSS daemon path. |
| Batch 20 GPG | GPG creates ~/.gnupg/random_seed during run 1, changing run 2 input state. | A fresh per-run GNUPGHOME passes L2. |
| Batch 21 Ruby | The host Ruby installation cannot load RbConfig and fails identically outside Hermit. | --disable-gems passes L2; this is host packaging, not a Hermit defect. |
| Batches 23 and 38 paste | paste blocks reading one process-substitution pipe while the other producer remains pending. | Requires scheduler support for the same cross-process pipe rendezvous class as FIFO/jobserver failures. |
| Batch 27 OpenSSL speed | The throughput benchmark reports host-performance-dependent work rates even though Detcore logs match. | Do not use a speed benchmark as deterministic-output coverage; test fixed-input cryptographic operations instead. |
| Batches 28 and 30 NSS identity | External nscd AF_UNIX readiness and cache state select different lookup paths; GID 65534 also lacks a host name. | Prefer numeric IDs or isolate/disable the external NSS daemon. |
| Batch 29 scp | This scp implementation has no `-V` option and exits 1 before verification run 2. | Capture the expected status or use a valid version probe; this is not nondeterminism. |
| Batch 32 short timeout | Virtual timeout expires while the dynamically linked child is still starting under instrumentation; native finishes before two wall-clock seconds. | Increase the timeout or test a prestarted/static child; the captured exit 124 is deterministic. |
| Batch 34 external web access | The host has no usable route to example.com; curl/wget exit nonzero before run 2. | External network success is outside Hermit's contract; local refused-connect paths pass L2. |
| Batch 35 named FIFO | The writer blocks in `openat(O_WRONLY)` while strict serialization prevents the reader from reaching its matching open. | Requires scheduler awareness of cross-process FIFO rendezvous, the same structural class as batches 13 and 18. |
| Batch 39 live system data | `lscpu`, `free`, and `ps` expose changing host CPU-frequency or memory values. | Virtualize or suppress those live fields; deterministic static topology, mount, disk, kernel, and page-size probes pass. |
| Batch 41 FIFO and process substitution | A blocking FIFO open or pipe read holds the serialized turn while the peer process that would satisfy it cannot run. | Requires scheduler awareness of cross-process rendezvous; simple pipes, coprocesses, IPC listing, sleeps, and regular-file fd operations pass. |
| Batch 42 Meta Python | Helper-thread startup orders parent futex progress and child RNG initialization differently; explicit application-level RNG seeding does not control that runtime startup. | Stock Python remains the established control for this failure class; the non-Python math tools pass. |
| Post-envp R/R pipelines | Replay can expose intermediate pipe payloads on stdout, allocate different fds for process substitution, or order pipe close and SIGCHLD differently. | Direct utility controls pass; console-fd tracking, replay fd allocation, and multi-process scheduling remain separate follow-ups. |

## Interpretation

- Across 394 recorded command outcomes, 323 reach L2 (82.0%), 66 do not
  reach L2, and 5 were not run. Repeated stress attempts are summarized in one
  row per scenario rather than inflating the command count.
- The strongest coverage is deterministic local computation, sequential
  compilation pipelines, normal multi-thread synchronization, text processing,
  compression, hashing, randomness virtualization, and signal delivery.
- Multi-process blocking rendezvous remain a structural gap: named FIFOs,
  parallel-make jobserver pipes, and paste process substitutions can deadlock
  when strict serialization prevents the peer process from running.
- Compiler and archive outputs are reliable when both verification runs start
  with identical visible filesystem state. Guest-isolated `/tmp` is the
  preferred output location; persistent project outputs can create false
  divergence when run 2 observes artifacts created by run 1.
- FAIL does not always mean nondeterministic output. The evidence separates
  scheduler divergence, serialization deadlocks, unvirtualized host state,
  stateful NSS, changing filesystem input, expected nonzero guest status,
  unsupported nested ptrace, host packaging failures, and intermittent results.
- Host `/tmp` is isolated from the guest. Stable input fixtures must live in
  an exposed working directory, while files created inside a Hermit run may
  safely use the guest-private `/tmp`.
- External networking is not covered. Hermit does not make a changing external
  network deterministic, and the batch host had no direct external route.
- Record/replay is tracked separately: the initial debug-binary matrix passed
  18/20, while 49 later pre-fix release-binary probes were dominated by one
  program-independent replay envp failure. PR #238 fixed that failure on main.
  Across the seven numbered post-fix matrices, 60/82 prescribed cases match
  recording. Remaining failures are distinct recorder ioctl/NULL-pointer,
  replay-fd, intermediate-output routing, and multi-process scheduling bugs.
