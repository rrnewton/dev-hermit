# vDSO clock status probes and neutralization cost

These probes support
[`ai_docs/vdso-host-clock-leak-1147-premise-audit_20260803.md`](../../ai_docs/vdso-host-clock-leak-1147-premise-audit_20260803.md).
They distinguish libc's vDSO-facing `clock_gettime` call from an explicit raw
syscall and measure the native cost of redirecting the former to the latter.
The measured medians are **30.44 ns/call through vDSO** and **113.36 ns/call
through a real syscall**, a **3.72x** redirect cost on this host. The audit also
separates #1147, whose Reverie pin already neutralizes vDSO clocks, from the
retired pure in-guest LiteInst path that demonstrably leaked under `--strict`.

```bash
cc -O2 -Wall -Wextra -Werror -o /tmp/vdso-clock-sequence \
  experiments/vdso_host_clock_leak_1147_20260803/src/clock_sequence.c
cc -O2 -Wall -Wextra -Werror -o /tmp/vdso-clock-bench \
  experiments/vdso_host_clock_leak_1147_20260803/src/clock_bench.c

worktrees/238b/hermit/target/debug/hermit --log=info run --tmp=/tmp \
  --backend ptrace --strict --verify /tmp/vdso-clock-sequence
worktrees/238b/hermit/target/debug/hermit --log=info run --tmp=/tmp \
  --backend dbi --strict --verify /tmp/vdso-clock-sequence

/tmp/vdso-clock-bench vdso 10000000
/tmp/vdso-clock-bench raw 10000000
```

`results.tsv` contains the raw performance samples. Functional clock sequences
and their exact interpretation are in the audit. The Hermit commands require a
build that includes the named backend.
