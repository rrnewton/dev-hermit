# Before building a log store: journald is NOT the wheel we can reuse (2026-08-04)

**Task:** `before-building-log-storage-check-what-we-already-have-journald-and-the-ledger-row`
**Author:** hermit-ci (analysis role — measured on devbig014, read-only + `sudo -n journalctl`).
**Owner question:** we run validates under `systemd-run --user`, so does journald already
capture them (making a home-grown `/tmp`→`ignored/` log store + rotation a reinvention)?

## Answer in one line

**No — do not build a log store, but not because journald covers it.** journald does **not**
hold validate logs (Storage=volatile, 10M cap, ~4-min window, and validate output is redirected
away from it). The thing that already makes a log store unnecessary **for attribution** is the
**ledger row + `first_error_line` sidecar**, which is durable and covers 193/193 reds today.

## The three numbers the owner asked for

### 1. Current journald retention — size and time
- Chef-managed config `/etc/systemd/journald.conf.d/90-chef.conf`:
  `Storage = volatile`, `RuntimeMaxUse = 10M`, `ForwardToSyslog = true`.
- `Storage = volatile` ⇒ the operative journal lives in **`/run/log/journal`** (tmpfs/RAM), not
  disk. The persistent `/var/log/journal` (329M) is a **frozen archive from the last persistent
  boot, 2026-05-22** — nothing new is written there.
- Volatile store measured **at its 10M cap** (8× 1.25M files). **Measured retention window under
  live 18-agent load: `14:20:19 → 14:24:08` ≈ 3 min 49 s.** Files rotate every ~20–35 s.
- A single validate `/tmp` log is often several MB — one validate alone can approach the entire
  10M system-wide journal budget.

### 2. Recent failing runs still retrievable from journald: **ZERO**
Three independent reasons, each sufficient:
- **(a) Output is redirected away from journald.** The canonical invocation (AGENTS.md L358,
  green-signal predicates) is
  `systemd-run --user … /bin/bash -c 'exec … ./validate.sh > <durable-log> 2>&1'`.
  The `> log 2>&1` redirect happens in the shell, so validate stdout/stderr go to the `/tmp`
  file, **not** to the unit's inherited journal fd. Verified on a real redirected validate unit
  (`dbibuild-cap8g-598976.service`): **0 content lines** in journald — only unit lifecycle.
- **(b) The window is shorter than a validate.** ~4-min retention < 5–40-min validate runtime;
  even the lifecycle lines are gone before the run finishes.
- **(c) The normal read path is permission-blocked.** There is **no per-user journald**
  (`systemctl --user status systemd-journald` → "could not be found"), and `newton` is not in
  `systemd-journal`/`adm`/`wheel`, so `journalctl --user -u <unit>` → *"No journal files were
  opened due to insufficient permissions."* The only working read is
  `sudo -n journalctl _SYSTEMD_USER_UNIT=<unit>.service` (passwordless sudo works here).

  *Correction to an earlier read:* a **bare** (non-redirected) `systemd-run --user` echo **is**
  captured and retrievable via `sudo journalctl _SYSTEMD_USER_UNIT=…` (probe `hci-jprobe2`
  confirmed). So the mechanism exists; it just isn't what validates use, and isn't reachable
  without sudo + the right field.

### 3. Reds that needed MORE than the first error line: **0 of 193**
- Landed read-side + sidecar (`ignored/validate-red-attribution.jsonl`): **193/193 red-node
  records attributable via `first_error_line`, 0 null** after the `attribute_reds --refill`
  backfill (commits 3baff73, de0ec4c, 575fe9d).
- The two owner-named fixtures attribute from the sidecar **alone**, no log:
  - `fedc81ed` → `build.runtime_release`, fault=**infrastructure**,
    `undefined reference to dynamorio::drmemtrace::op_infile` (0-byte object → link failure).
  - `1fad135d` → `build.runtime_release`, fault=**code**,
    `cannot update the lock file … because --locked was passed` (stale liteinst Cargo.lock).
- Both diagnosable from one line. Full logs are needed only for rare **deep investigation**, not
  for attribution.

## Decision

- **Do not build a `/tmp`→`ignored/` log store + rotation subsystem.** journald can't be
  reused for validate logs (volatile / 10M / ~4 min / redirected-away / read-blocked), but the
  **ledger row + `first_error_line` sidecar already solves durable attribution** and survives
  `/tmp` eviction. Building storage would solve a problem the row work already shrank.
- There is **no "third copy"** to reinvent: journald does not hold the content, so today there is
  exactly **one** ephemeral copy (`/tmp`) plus the durable **row/sidecar** distilled from it.
- **Residual gap** = rare deep investigation after `/tmp` eviction. If that ever bites, the
  minimal bounded fix (NOT a rotation subsystem) is: at ledger/attribution time, copy the
  **RED run's** `/tmp` log to `ignored/validate-logs/<commit>-<node>.log`, **reds only**, pruned
  once the sidecar has a confident `first_error_line`. Bounded by red count (dozens), no rotation
  logic. Reconsider only if measured deep-investigation misses accumulate.
- Config alternatives (all require changing chef-managed state / group membership, so out of an
  agent's hands and higher-cost than the row that already works): raise `RuntimeMaxUse`, set
  `Storage=persistent`, add `newton` to `systemd-journal`, and drop the `> log 2>&1` redirect so
  journald captures validate output. Not recommended over the sidecar for attribution.

## Reproduction
```
sudo -n cat /etc/systemd/journald.conf.d/90-chef.conf          # Storage=volatile, RuntimeMaxUse=10M
sudo -n du -sh /run/log/journal                                 # ~10M (at cap)
sudo -n journalctl -D /run/log/journal -o short-iso | head -1   # window start
sudo -n journalctl -D /run/log/journal -o short-iso | tail -1   # window end (~4 min later)
sudo -n journalctl _SYSTEMD_USER_UNIT=<validate-unit>.service | wc -l   # 0 for redirected validate
journalctl --user -u <unit>                                     # "insufficient permissions" (no per-user journald)
```
