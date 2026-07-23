# Coverage matrix: 2026-07-23

This report consolidates measured program coverage from the 2026-07-23
TaskGraph notes. It is a program-level view; the scenario-level source remains
`ai_docs/transient/strict-compat-matrix.md`.

## Run context

- Strict assurance: `PASS L2` means both ptrace runs completed under
  `target/release/hermit run --strict --verify --` and the normalized logs
  matched. Default logging and no scheduler relaxations were used unless a row
  says otherwise.
- Record/replay assurance: `PASS R/R` means
  `target/release/hermit record start --verify --` completed recording, replay,
  and comparison on the ptrace backend with default logging.
- Commands below omit those common Hermit prefixes.
- Results span the exact SHAs recorded in each task note. The most recent
  current-main rows were measured at `4f8afa5` or `fb6a878`; older strict batches
  record their own SHAs in the scenario-level matrix.
- `MIXED` is not a pass: different tested command shapes produced different
  results. `NOT MEASURED` means no matching result was found; it is not inferred
  from another mode.

## Aggregate evidence

| Evidence set | Pass | Fail | Unresolved / not run | Total | Notes |
|---|---:|---:|---:|---:|---|
| Strict batches 1-59 | 462 | 70 | 5 | 537 | Existing scenario matrix. |
| Strict batches 60-69 | 58 | 10 | 6 | 74 | Batch 65 closed before its corrected five-case rerun; autoconf was absent in batch 69. |
| **Strict total** | **520** | **80** | **11** | **611** | 86.7% pass among cases with a final verdict. |
| Post-envp R/R batches 1-15 | 121 | 50 | 0 | 171 | Prescribed cases only; supplemental controls excluded. |
| Post-envp R/R batches 16-21 | 45 | 3 | 0 | 48 | Shell, compiled, threaded, fork/IPC, signal, and error cases. |
| **Post-envp R/R total** | **166** | **53** | **0** | **219** | 75.8% pass. Later one-off rows below are not added to avoid double counting. |

## Core and text utilities

| Program / workload | Command | --strict L2 | R/R | Notes |
|---|---|---|---|---|
| echo | `/bin/echo hello` | PASS L2 | PASS R/R | Baseline single-process output. |
| cat | `/bin/cat /etc/hostname` | PASS L2 | PASS R/R | Direct file read passes both modes. |
| wc | `/usr/bin/wc lines.txt` | PASS L2 | PASS R/R | Direct invocation passes; pipeline variants depend on replay fd ordering. |
| head | `/usr/bin/head -3 lines.txt` | PASS L2 | PASS R/R | Direct bounded read. |
| tail | `/usr/bin/tail -3 lines.txt` | PASS L2 | FAIL | Replay fd allocation/close ordering diverges. |
| seq | `seq 1 100` | PASS L2 | PASS R/R | Direct invocation passes. |
| sort | `/usr/bin/sort fruits.txt` | PASS L2 | PASS R/R | Direct file sort passes. |
| sort pipeline | `seq 1 1000 &#124; sort -n &#124; tail -5` | PASS L2 | FAIL | Replay pipeline exposes fd/SIGCHLD ordering gaps. |
| uniq | `/usr/bin/uniq sorted.txt` | PASS L2 | PASS R/R | Direct file input passes. |
| tr | `tr 'a-z' 'A-Z' < /etc/hostname` | PASS L2 | PASS R/R | Direct stdin/file transformation passes. |
| bounded yes | `yes hello &#124; head -5` | PASS L2 | FAIL | R/R can time out or mishandle SIGPIPE/output routing. |
| cut | `cut -d: -f1 /etc/passwd` | PASS L2 | PASS R/R | Direct form passes. |
| cut pipeline | `cut ... &#124; sort &#124; head` | PASS L2 | FAIL | Replay can leak upstream payload and allocate different fds. |
| paste | `paste` on stable files | PASS L2 | PASS R/R | Direct stable-file form passes. |
| paste process substitution | `paste -d, <(seq 1 3) <(seq 4 6)` | FAIL | FAIL | Strict run can hang on serialized producers; replay also diverges. |
| comm process substitution | `comm -3 <(echo ...) <(echo ...)` | PASS L2 | NOT MEASURED | The tested bounded command completed at L2. |
| join | `join` on sorted fixture files | PASS L2 | NOT MEASURED | Covered by text-processing strict batch. |
| expand | `expand /etc/hostname` | PASS L2 | NOT MEASURED | Direct file input. |
| fold | `fold -w 40 /etc/passwd` | PASS L2 | FAIL | Direct strict form passes; R/R pipeline form leaked producer bytes. |
| fmt | `fmt -w 40 /etc/passwd` | PASS L2 | PASS R/R | Direct/bounded forms pass both modes. |
| nl | `nl /etc/hostname` | PASS L2 | PASS R/R | Direct form passes. |
| rev | `rev /etc/hostname` | PASS L2 | PASS R/R | Direct form passes. |
| tac | `tac /etc/hostname` | PASS L2 | NOT MEASURED | Strict direct form passes. |
| split | `split -l 2 input part-; cat part-*` | PASS L2 | NOT MEASURED | Uses isolated temporary output. |
| sed | `sed -n '1,5p' /etc/passwd` | PASS L2 | PASS R/R | Direct form passes. |
| awk | `awk -F: '{print $1}' /etc/passwd` | PASS L2 | PASS R/R | Direct form passes. |
| grep | `grep -E '^[a-z]+:' /etc/passwd` | PASS L2 | PASS R/R | Direct form passes. |
| xargs | `xargs echo < /etc/hostname` | PASS L2 | MIXED | Direct cases pass; multi-stage replay pipelines can diverge. |
| diff, equal | `diff a b` | PASS L2 | NOT MEASURED | Equal files exit zero. |
| diff, unequal | `run --strict --verify --verify-allow both -- diff a b` | PASS L2 | NOT MEASURED | Expected exit 1 requires the explicit verification policy. |
| tee | `tee` with direct stable input | PASS L2 | PASS R/R | Direct form passes; combined pipelines remain a separate risk. |

## Shell and process composition

| Program / workload | Command | --strict L2 | R/R | Notes |
|---|---|---|---|---|
| Bash loop | `bash -c 'for i in 1 2 3; do echo $i; done'` | PASS L2 | PASS R/R | Builtin-only loop. |
| Bash arithmetic | `bash -c 'echo $((2**10))'` | PASS L2 | PASS R/R | No child process. |
| Bash associative array | `bash -c 'declare -A m; m[k]=v; echo ${m[k]}'` | PASS L2 | PASS R/R | Builtin-only. |
| simple pipeline | `bash -c 'echo hello &#124; cat'` | PASS L2 | FAIL on main; PASS on draft #236 | PR #236 follow-up passed the full 27-test R/R target but is not landed. |
| three-stage pipeline | `cat /etc/passwd &#124; grep root &#124; cut -d: -f1` | PASS L2 | FAIL | Main R/R can expose intermediate output or reorder closes. |
| command substitution | `bash -c 'A=$(echo hello); echo "$A world"'` | PASS L2 | FAIL | Replay leaks command-substitution pipe payload in failing cases. |
| process substitution, diff | `bash -c 'diff <(echo a) <(echo b); true'` | PASS L2 | FAIL | Strict bounded form passes; R/R fd ordering diverges. |
| process substitution, read | `bash -c 'read line < <(echo subprocess); echo $line'` | FAIL | NOT MEASURED | Strict run 1 hangs waiting for a serialized producer. |
| background wait | `bash -c 'sleep 0.01 & wait $! && echo waited'` | PASS L2 | PASS R/R | Bounded child and wait. |
| parallel xargs | `seq 1 100 &#124; xargs -P4 -I{} echo {}` | PASS L2 | NOT MEASURED | Strict batch 50 passed. |
| named FIFO rendezvous | `mkfifo p; echo x >p & cat p; wait` | FAIL | NOT MEASURED | Serialized writer blocks before the reader reaches its peer open. |
| pipefail status | `bash -c 'set -o pipefail; false &#124; true; echo $?'` | PASS L2 | PASS R/R | Status is captured while the outer guest exits zero. |
| ERR trap | `bash -c 'trap "echo ERR" ERR; false; echo after'` | PASS L2 | PASS R/R | Error handling matches. |
| SIGUSR1 self-trap | `bash -c 'trap "echo got" USR1; kill -USR1 $$; echo done'` | PASS L2 | PASS R/R | Isolated signal case passes. |
| timeout status | `bash -c 'timeout 1 sleep 10; echo timeout=$?'` | PASS L2 | PASS R/R | Deterministic captured exit 124. |

## Git, compilers, and build tools

| Program / workload | Command | --strict L2 | R/R | Notes |
|---|---|---|---|---|
| raw Git version | `/usr/bin/git --version` | PASS L2 | PASS R/R | Stock Git 2.52.0; 909/909 strict messages and 566/566 R/R messages. |
| raw Git init | `/usr/bin/git init /tmp/test_raw_git` | PASS L2 | FAIL | R/R records, then diverges on lazy locale catalog access. |
| raw Git status | `/usr/bin/git -C /tmp/test_raw_git status` | PASS L2 | FAIL | Stable mounted fixture passes strict; replay diverges reproducibly. |
| raw Git config | `/usr/bin/git -C /tmp/test_raw_git config user.email test@test.com` | PASS L2 | NOT MEASURED | Idempotent preseeded fixture. |
| raw Git add+commit | guest-private init/add/commit transaction | PASS L2 | NOT MEASURED | Six L2 passes in an earlier task; reproducible commit SHA. |
| raw Git log | `/usr/bin/git -C /tmp/git_advanced log --oneline` | PASS L2 | NOT MEASURED | Stable two-commit fixture. |
| raw Git diff | `/usr/bin/git -C /tmp/git_advanced diff HEAD~1..HEAD` | PASS L2 | NOT MEASURED | Stable read-only fixture. |
| Meta Git wrapper | `/usr/local/bin/git --version` | FAIL | NOT MEASURED | Runtime/telemetry threads diverge; stock Git is the established control. |
| gcc compile, isolated | `gcc -c util.c -o /tmp/util.o` | PASS L2 | FAIL for full compile | Per-run `/tmp` avoids persistent output state; R/R full compiler flows still diverge. |
| gcc modes | `gcc -S`, `gcc -E`, `cpp`, `cc1` | PASS L2 | PASS R/R | Compiler front-end/version modes pass R/R; full generated-binary workflows are weaker. |
| gcc persistent output | `gcc -c util.c -o project/util.o` | FAIL | NOT MEASURED | Run 2 observes run 1's output artifact. |
| rustc version | `rustc --version` | PASS L2 | FAIL | R/R replay exhausts an epoll worker-thread event stream. |
| rustc optimized compile | `rustc -O -o /tmp/rust-app app.rs` | PASS L2 | NOT MEASURED | Batch 18 passed with isolated output. |
| rustc current-main compile retest | `rustc -o /tmp/hello_rs /tmp/hello.rs` with source bind | FAIL 0/3 | NOT MEASURED | Post-PR #221 runs still diverged at linker vfork scheduling. |
| GNU as | `as --version` or assemble fixture | PASS L2 | PASS R/R | Version and compiled fixture coverage pass. |
| GNU ld | link freestanding fixture in `/tmp` | PASS L2 | PASS R/R | Isolated output. |
| make version | `make --version` | PASS L2 | PASS R/R | R/R confirmed twice. |
| make serial build | `make -j1` with isolated output | PASS L2 | NOT MEASURED | Output is byte-identical across fresh builds. |
| make parallel, independent targets | `make -f simple.mk -j4` | PASS L2 | NOT MEASURED | Batch 50's four independent targets pass. |
| make parallel C build | `make -j4` with concurrent compiler children | FAIL | NOT MEASURED | Run 1 hangs in concurrent vfork/jobserver scheduling. |
| CMake version | `cmake --version` | PASS L2 | PASS R/R | Version-only path passes twice. |
| CMake configure | `cmake -S . -B build` | FAIL before L1 | NOT MEASURED | Libuv epoll child-wait path livelocks even without strict. |
| Cargo tests | `CARGO_NET_OFFLINE=true cargo test` in isolated project | PASS L2 | NOT MEASURED | Three test cases and matching logs. |
| Go build | `GOFLAGS=-p=1 go build -o /tmp/goapp` | FLAKY | NOT MEASURED | Seven of eight L2 attempts passed; one scheduling divergence. |
| Go test | `GOMAXPROCS=1 GOFLAGS='-count=1 -p=1' go test ./...` | FAIL | NOT MEASURED | Stalls around clone/vfork, futex, and nanosleep. |

## Interpreters and data tools

| Program / workload | Command | --strict L2 | R/R | Notes |
|---|---|---|---|---|
| stock Python version | `/usr/bin/python3 --version` | PASS L2 | NOT MEASURED | Stock interpreter is the stable control. |
| Meta Python version | `python3 --version` | PASS L2 | FAIL | Recorder panics on ioctl `0x8946`; guest output itself is correct. |
| stock Python JSON | `/usr/bin/python3.9 -c 'import json; ...'` | PASS L2 | NOT MEASURED | Stock runtime control passes. |
| Meta Python JSON/CSV | `python3 -c ...` | FAIL | NOT MEASURED | Helper-thread/RNG startup scheduling diverges. |
| Lua | `lua -e 'print(2+2)'` | PASS L2 | PASS R/R | Direct program and `lua -v` pass. |
| m4 | `m4 <<< 'define(X,42)X'` | PASS L2 | PASS R/R | Here-string input passes. |
| bc | `bc -q <<< '2+2'` | PASS L2 | PASS R/R | Direct stdin form passes. |
| Perl | `perl -e 'print 2+2, "\n"'` | PASS L2 | PASS R/R | Direct interpreter path passes. |
| Ruby without broken gems | `ruby --disable-gems -e ...` | PASS L2 | PASS R/R | Default host Ruby packaging is broken natively; disabling gems is the valid control. |
| Node.js JSON | `node -e 'console.log(JSON.stringify(...))'` | PASS L2 | FAIL in broader R/R | Node recording has hit unsupported network ioctls/clone paths. |
| sqlite3 | `sqlite3 :memory: 'select 2+2;'` | PASS L2 | PASS R/R | Direct in-memory queries pass. |
| jq | `jq` over fixed JSON | PASS L2 | PASS R/R | Direct fixed input passes. |
| Java version | `java -version` | PASS L2 | PASS R/R | Focused post-envp validation passed. |
| javac compile | compile and run Hello class | PASS L2 | NOT MEASURED | L2 passes; earlier R/R recording did not complete. |

## Compression and archives

| Program / workload | Command | --strict L2 | R/R | Notes |
|---|---|---|---|---|
| gzip version | `gzip --version` | PASS L2 | PASS R/R | R/R confirmed twice. |
| gzip round trip | in-guest `gzip &#124; gunzip` with assertion | PASS L2 | MIXED | Direct gzip/gunzip R/R passes; multi-stage workflows can desynchronize. |
| bzip2 version | `bzip2 --version` | PASS L2 | NOT MEASURED | Current-main L2 retest passed. |
| bzip2 round trip | in-guest `bzip2 &#124; bunzip2` | PASS L2 | NOT MEASURED | Deterministic content round trip. |
| xz version | `xz --version` | PASS L2 | NOT MEASURED | Current-main L2 retest passed. |
| xz round trip | in-guest `xz &#124; xz -d` | PASS L2 | NOT MEASURED | Deterministic content round trip. |
| zstd round trip | in-guest `zstd &#124; zstd -d` | PASS L2 | NOT MEASURED | Batch 49 passed. |
| tar version | `tar --version` | PASS L2 | PASS R/R | Version-only path passes twice. |
| tar numeric-owner create | `tar --numeric-owner -cf /tmp/test.tar /etc/hostname` | PASS L2 | NOT MEASURED | Avoids NSS owner-name lookups. |
| tar default owner lookup | `tar cf /tmp/test.tar /etc/hostname` | FAIL | NOT MEASURED | Stateful NSS/nscd polling diverges. |
| tar isolated round trip | create/list/extract entirely inside guest `/tmp` | PASS L2 | FAIL for broader workflow | Strict isolation-aware controls pass; R/R filesystem/fd sequence can desynchronize. |
| zip/unzip | create archive and stream member | PASS L2 | NOT MEASURED | Strict batch 7 passed. |
| cpio stream | create/list newc stream | PASS L2 | NOT MEASURED | Strict archive batches pass. |

## Containers and system programs

| Program / workload | Command | --strict L2 | R/R | Notes |
|---|---|---|---|---|
| podman version | `podman --version` | PASS L2 | FAIL | Replay diverges on worker thread 3 at event 327. |
| crun version | `crun --version` | PASS L2 | FAIL | Replay diverges on worker thread 3 at event 94. |
| unshare | `unshare --pid --fork -- /bin/echo hello` | PASS L2 | NOT MEASURED | PID namespace creation passes. |
| hostname | `hostname` | PASS L2 | PASS R/R | Strict output is virtualized as `hermetic-container.local`. |
| hostname FQDN | `hostname -f` | PASS L2 | FAIL on main; focused fix in draft #261 | Main recorder panics on successful NULL/zero-length `getsockopt`. |
| uname | `uname -a` | PASS L2 | PASS R/R | Static kernel identity passes. |
| arch | `arch` | PASS L2 | PASS R/R | Direct system identity passes. |
| nproc | `nproc` | PASS L2 | PASS R/R | Direct topology result passes. |
| getconf | `getconf PAGESIZE` | PASS L2 | PASS R/R | Static page-size query. |
| uptime | `uptime` | PASS L2 | PASS R/R | Virtualized time result passes. |
| df | `df -h` | PASS L2 in current batch | PASS R/R | A prior host had broken mounts; current system-information batch passed. |
| mount | `mount` | PASS L2 | PASS R/R | Mount listing passes. |
| ps | `ps aux` | FAIL | NOT MEASURED | Live VSZ/RSS values differ. |
| free | `free -m` | FAIL | NOT MEASURED | Live host memory counters differ. |
| lscpu | `lscpu` | FAIL in live-data batch | NOT MEASURED | CPU frequency percentage changes; static controls pass. |
| id/whoami | `id`; `whoami` | MIXED | PASS R/R | Strict identity can enter live NSS; R/R system-information retest passed. |
| nice | `nice -n 5 echo hello` | PASS L2 | PASS R/R | Deterministic priority request and output. |

## Main gaps exposed by the matrix

1. **Replay completeness trails strict execution.** Concurrent real programs
   such as Podman, crun, rustc, Meta Python, raw Git workflows, and Node expose
   missing or misordered worker-thread epoll, ioctl, locale, fd, and close
   events.
2. **Cross-process rendezvous remains structural.** FIFO opens, process
   substitution, CMake/libuv child waiting, make jobserver traffic, and some
   pipelines can block the serialized turn needed by their peer.
3. **Filesystem isolation is part of the contract.** Compilers, Git, tar, and
   build tools pass when both verify runs begin with equivalent inputs and use
   per-run output. Persistent mounted outputs produce valid but different run-2
   state.
4. **Live host services and counters are not deterministic inputs.** NSS/nscd
   owner lookup, CPU frequency, memory counters, and process RSS/VSZ require
   isolation, numeric output, or explicit virtualization.
5. **Binary provenance matters.** Stock `/usr/bin/git` passes L2 while the Meta
   wrapper does not; stock Python controls pass workloads that the threaded
   Meta runtime can fail during startup.

## Evidence sources

- `ai_docs/transient/strict-compat-matrix.md`, batches 1-59 and post-envp R/R
  batches 1-15.
- TaskGraph notes for strict batches 60-69 and post-envp R/R batches 16-21.
- TaskGraph notes: `impl-test-raw-git-strict`, `impl-test-git-advanced`,
  `impl-rr-raw-git`, `impl-test-hermit-rust-compile-retest`,
  `impl-test-hermit-make-parallel`, `impl-test-hermit-cmake-project`,
  `impl-test-compression-tools`, `impl-rr-make-cmake`,
  `impl-rr-compression-tools`, and `impl-rr-container-python`.
