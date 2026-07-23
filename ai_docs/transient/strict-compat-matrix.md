# Strict and verify compatibility matrix

This is the consolidated result of the 2026-07-23 compatibility expansion
batches 1 through 24. It reports the commands recorded in TaskGraph by
`impl-strict-compat-expansion` (batch 1) and
`impl-strict-compat-batch2` through `impl-strict-compat-batch24`.
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
- Command column: commands omit the common
  `target/release/hermit run --strict --verify --` prefix unless a Hermit
  option such as `--verify-allow both` matters.
- Scope: counts below are command outcomes, including recorded controls and
  workarounds. FAIL means the command did not reach L2, whether because of
  nondeterminism, a hang, a guest error, host state, or harness policy.

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
| **Total** | | **190** | **42** | **3** |

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
| Batch 10 strace | Nested ptrace is unsupported under Hermit's ptrace backend. | No ptrace-backend fix recorded; use a non-nested tracing approach. |
| Batch 10 timeout | Host SIGALRM uses wall time while guest sleep advances on virtual time. | No fix recorded in the batch. |
| Batch 11 Git | Git startup spins in `sched_yield` under strict sequentialization, generates multi-gigabyte logs, and never completes run 1. | No fix validated by batch 11. |
| Batch 13 named FIFO | Blocking FIFO open/read waits for a peer process that strict serialization does not schedule. | No fix recorded; requires scheduler awareness of cross-process FIFO rendezvous. |
| Batch 13 Meta Python pipeline | The Meta Python runtime diverges during threaded startup even without a pipe. | Stock CPython is the established control; the pipeline itself is not the failure. |
| Batch 14 nslookup and nc | Both commands deterministically exit nonzero under network isolation, so default verify does not start run 2. | Use an expected-exit verification policy when the nonzero result is the intended contract. |
| Batches 15, 17, and 23 Meta Python | Clone/futex and RNG-seed ordering diverges in the multithreaded Meta runtime startup. | Stock /usr/bin/python3 controls pass; no Meta-runtime scheduling fix was established. |
| Batches 16 and 22 compiler outputs | Verify runs share persistent visible filesystem state; run 2 observes artifacts created by run 1. | Put outputs in guest-isolated /tmp, pre-stage identical outputs, or copy the project into per-run /tmp. |
| Batch 17 Go tests | Go test stalls around clone/vfork, futex, and nanosleep before producing an L1 result. | No fix recorded; single-P and warm-cache controls still stalled. |
| Batch 18 parallel make | GNU make jobserver pipe rendezvous deadlocks under serialized scheduling. | Use -j1; scheduler support for cross-process blocking pipes is required for -jN. |
| Batch 18 Go build | Residual runtime/subprocess scheduling divergence appeared once in eight L2 attempts. | Treat as intermittent, not a reliable L2 pass. |
| Batch 19 capsh | The number of NSCD AF_UNIX exchanges differs between runs. | Isolate or disable the external NSS daemon path. |
| Batch 20 GPG | GPG creates ~/.gnupg/random_seed during run 1, changing run 2 input state. | A fresh per-run GNUPGHOME passes L2. |
| Batch 21 Ruby | The host Ruby installation cannot load RbConfig and fails identically outside Hermit. | --disable-gems passes L2; this is host packaging, not a Hermit defect. |
| Batch 23 paste | paste blocks reading one process-substitution pipe while the other producer remains pending. | Requires scheduler support for the same cross-process pipe rendezvous class as FIFO/jobserver failures. |

## Interpretation

- Across 235 recorded command outcomes, 190 reach L2 (80.9%), 42 do not
  reach L2, and 3 were not run. Repeated stress attempts are summarized in one
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
