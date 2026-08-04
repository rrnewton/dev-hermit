# cpu_timeout enforcement verified (plant both ways)

**Question:** does the DAG runner's per-node `cpu_timeout` actually kill a CPU
breach loudly, and leave a compliant workload alone — under real cgroup boxing?

**Method:** a 3-node DAG (`probe.json`) run boxed FAIL-CLOSED (no
`--allow-cgroup-failure`), keep-going, on devbig014. See `metadata.json`.

**Result (see `step_profiles.csv`, cgroup-attributed):**

| node | cpu_timeout | wall timeout | outcome | cpu.usage_usec | verdict |
|---|---|---|---|---|---|
| probe.cpu_breach   | 2s | 60s | KILLED, returncode -9, `cpu_timed_out=True`, reason `CPU-TIMEOUT >2s cpu` | 2003822 (2.00 CPU-s) | breach killed loudly at budget, far before wall |
| probe.compliant_idle | 2s | 60s | PASS, `cpu_timed_out=False` | 2731 (0.003 CPU-s) | survived 4.0s WALL on ~0 CPU under a 2s CPU budget → cpu_timeout is wall/load-immune |
| probe.compliant_cpu  | 5s | 60s | PASS, `cpu_timed_out=False` | 423804 (0.42 CPU-s) | real but bounded CPU work under budget → not killed |

**Conclusion:** at the runner version hermit records (`ec4ddf07`, v0.12.0),
`cpu_timeout` is enforced (Python engine) via a 1 Hz `cpu.stat usage_usec` poll +
whole-cgroup reap. It fires ONLY on CPU work, is immune to idle wall time, and
reports a distinct `CPU-TIMEOUT` reason (not wall `TIMEOUT`). Enforcement is
INERT when unboxed (`cpu_stats` is None) — so it is live on the local and
capable self-hosted lanes, decorative on hosted-portable (which runs unboxed).
