# QEMU-Linux-under-Hermit: in-VM network determinism verified (L2)

- **Date:** 2026-07-27
- **Task:** `frontier-linux-hermit-network` (P1 frontier — network determinism inside the linux-hermit QEMU VM)
- **Milestone:** **L2 (`--strict --verify`, bitwise-identical repeat run)** for the
  guest kernel's **networking stack** — DNS, TCP sockets, and HTTP — exercised
  entirely **inside** a QEMU-emulated Linux VM running under Hermit.
- **Backend:** `ptrace` (default). **Relaxations:** none (strict, sequentialized).

## Question

The existing strict QEMU-Linux boot (`qemu_strict_l2_boot_20260727`, PR #992)
runs with `-nic none` and exercises **no** networking. This task asks: can
network operations — DNS, HTTP, socket operations — be made **deterministic
inside the hermit QEMU VM**, and proven at L2?

Framing that matters: a real DNS lookup or HTTP fetch to the internet is
*inherently* nondeterministic (external servers, wall-clock timing, route
changes), so it can never be an L2 target without record/replay. The provable,
meaningful milestone is **network traffic that stays internal to the guest** —
the guest kernel's own loopback TCP/IP stack. If that is deterministic, then
initial sequence numbers, ephemeral port selection, checksums, and payload
bytes all reproduce exactly across two runs.

## Method

New guest init + harness (this PR: rrnewton/hermit#1019), on the same proven
freestanding boot path as `strict_l2_test.sh`:

- `tests/shared-futex-verify/qemu_net_init.c` — freestanding (raw-syscall, no
  libc) guest `init` that, after the kernel boots, performs:
  1. **AF_UNIX socketpair echo** → `QEMU_NET_SOCKETPAIR_OK`
  2. **`/etc/hosts` name resolution** (no NSS, no network) → `QEMU_NET_DNS_OK`
  3. **loopback bring-up** via `SIOCSIFADDR`/`SIOCSIFNETMASK`/`SIOCSIFFLAGS`
     ioctls → `QEMU_NET_LO_UP`
  4. **AF_INET TCP client/server handshake** over `127.0.0.1:8080`
     (listen → fork → connect → accept) → `QEMU_NET_TCP_OK`
  5. **HTTP** `GET / HTTP/1.0` request + `200 OK`/`HELLO` response over that
     TCP connection → `QEMU_NET_HTTP_OK`
  then `QEMU_NET_ALL_OK`, `sync`, and power off.
- `tests/qemu-boot/strict_l2_network_test.sh` — builds an initramfs
  (`init` + `/etc/hosts`), runs a **strict boot oracle** asserting every marker,
  then reruns the same command under **`--verify`** for the L2 comparison.

Run:

```bash
cd ~/work/dev-hermit/worktrees/275/hermit   # (or any hermit checkout at the SHA)
env HERMIT_BIN="$PWD/target/release/hermit" \
    KERNEL_IMAGE=/boot/vmlinuz \
    QEMU_BIN=/usr/local/bin/qemu-system-x86_64 \
    QEMU_L2_PHASE_TIMEOUT_SECONDS=360 \
    bash tests/qemu-boot/strict_l2_network_test.sh
```

Exact guest command (from the harness):

```
qemu-system-x86_64 -nodefaults -nic none -m 256M -accel tcg,thread=single \
  -smp 1 -icount shift=0,sleep=off -rtc base=utc,clock=vm \
  -kernel /boot/vmlinuz -initrd <run>/initramfs.cpio.gz \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

`-nic none` is intentional: there is no QEMU-level NIC. **All** networking is
the guest kernel's own loopback stack, so nothing leaves the deterministic
sandbox.

## Results

**PASS at L2 (ptrace backend).**

Boot oracle (`boot_oracle_console.txt`):

```
Run /init as init process
SHARED_FUTEX_QEMU_KERNEL_OK release=6.17.13-0_fbk0... machine=x86_64
QEMU_NET_SOCKETPAIR_OK bytes=4 data=PING
QEMU_NET_DNS_OK localhost=127.0.0.1
QEMU_NET_LO_UP
QEMU_NET_TCP_OK proto=tcp addr=127.0.0.1:8080
QEMU_NET_HTTP_OK status=200 body=HELLO
QEMU_NET_ALL_OK
reboot: Power down
```

L2 verify (`verify_comparison_summary.txt`):

```
Logs contain 516400 | 516400 messages total
Logs contain 459540 | 459540 detcore-specific messages
Logs contain 363689 | 363689 DETLOG & scheduler COMMIT messages
Done processing logs, no substantive differences found.
:: Success: deterministic. Determinism verified.
```

Run1 and Run2 agree on every counter with no substantive DETLOG difference —
the entire in-VM network exchange (loopback ifconfig, TCP three-way handshake,
HTTP request/response, and `/etc/hosts` resolution) is **bitwise-deterministic**
at L2.

## Interpretation

- **In-VM networking is L2-deterministic under Hermit.** The guest kernel's
  loopback TCP/IP path — including ISN generation, ephemeral ports, and
  checksums, all of which draw on the kernel RNG / virtual clock — reproduces
  exactly, because Hermit determinizes the QEMU host process and QEMU emulates
  the guest deterministically via single-thread TCG + icount.
- This is the network analogue of the boot milestone
  (`qemu_strict_l2_boot_20260727`): the boot proved kernel bring-up is L2; this
  proves the **network subsystem** reached from userspace is L2.

## Scope / caveats

- **Loopback / in-guest only, by design.** DNS is resolved from `/etc/hosts`;
  HTTP and TCP are over `127.0.0.1`. This is the deterministic-by-construction
  surface.
- **External-egress networking is explicitly out of scope** and is *not* an L2
  target: a real DNS/HTTP call through a QEMU NIC + slirp would make host-side
  `socket()/connect()/sendto()` syscalls whose responses depend on the outside
  world. Making *that* deterministic requires record/replay of QEMU's host
  socket traffic — a separate future milestone, not a `--verify` bitwise result.
- **Backend scope:** ptrace only. KVM/DBI parity for in-VM networking is not
  claimed here.

## Recommended follow-ups

1. Wire `strict_l2_network_test.sh` into a hardware-dependent CI lane
   (kernel + QEMU) alongside `strict_l2_test.sh`.
2. KVM-backend parity for the in-VM network exchange.
3. External-egress record/replay: run QEMU user-mode networking (slirp) under
   Hermit `record`/`replay` and verify the guest sees identical DNS/HTTP bytes
   on replay (record/replay determinism, distinct from `--verify` L2).

## Reproduction

See `metadata.json` for SHAs/host. Rerun the exact `env ... bash
tests/qemu-boot/strict_l2_network_test.sh` command above from a hermit checkout
at the recorded SHA. The full `--log info` DETLOG dumps (tens of MB) are
intentionally **not** committed (>2 MiB); regenerate by rerunning.
