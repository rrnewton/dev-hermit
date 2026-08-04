/*
 * rpc_ceiling_bench.c — CEILING microbench for the det-mode coordinator RPC hop.
 *
 * Task: perf_coordinator_roundtrip_reduction (lever B: shared-mem ring + futex).
 * Scope: ai_docs/2026-08-04-coordinator-roundtrip-reduction-scope.md
 *
 * QUESTION IT ANSWERS: how much per-hop kernel cost could lever B (replace the
 * UDS blocking-client + tokio-epoll round-trip with a shared-mem word + futex)
 * remove — BEFORE writing any transport? Every comparand pays the SAME two
 * context switches (the cross-thread hop); lever B does NOT remove the ctx
 * switch (only lever A does). So the measured deltas below bound the removable
 * transport/framing/reactor overhead stacked on top of that shared cost.
 *
 * THREE COMPARANDS (same 2-thread ping-pong skeleton):
 *   uds_full : getpid+gettid + write(4B)+write(payload) + read(1B)+read(3B)+read(payload)
 *              == EXACT current ~7-syscall guest hop (blocking_client.rs:114-147).
 *   uds_lean : 1 write + 1 read, no getpid/gettid, no probe framing
 *              == guest-side trim to ~3 syscalls WITHOUT a transport rewrite.
 *   futex    : shared-mem seq word + SYS_futex FUTEX_WAIT/FUTEX_WAKE ping-pong
 *              == lever B target transport.
 *
 * DECOMPOSITION:
 *   d(uds_full -> uds_lean) = cheap guest-side trim (no rewrite).
 *   d(uds_lean -> futex)    = transport-swap-SPECIFIC saving (what a rewrite buys).
 *   d(uds_full -> futex)    = full lever-B ceiling.
 *
 * CAVEAT baked into interpretation: a plain BLOCKING UnixStream on both sides
 * UNDERSTATES the real cost — the real coordinator server is tokio-epoll
 * (server.rs:132/:214), adding a reactor epoll_wait per hop. So the uds numbers
 * are a CONSERVATIVE (lower-bound) ceiling: if even this delta is small, a
 * transport rewrite is not worth it.
 *
 * Measurement discipline (benchmark skill): K=1 process, medians + IQR, warmup,
 * per-round-trip timing via CLOCK_MONOTONIC, both same-core-pinned and cross-core.
 *
 * DO NOT RUN under the fleet validate pause — it loads two cores for a few
 * seconds and would perturb a host-wide-zero red/green determination.
 *
 * Build:  cc -O2 -pthread -o rpc_ceiling_bench rpc_ceiling_bench.c
 * Run:    ./rpc_ceiling_bench [iters] [warmup] [payload_bytes]
 *         (defaults: iters=200000 warmup=20000 payload=64)
 * Emits CSV rows to stdout: mode,pin,iters,payload,p50_ns,p25_ns,p75_ns,min_ns,p99_ns
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>
#include <errno.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <linux/futex.h>
#include <sys/types.h>

/* ---- timing ---- */
static inline uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static int cmp_u64(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

static void report(const char *mode, const char *pin, uint64_t *lat, size_t n,
                   size_t payload) {
    qsort(lat, n, sizeof(uint64_t), cmp_u64);
    uint64_t p25 = lat[n / 4], p50 = lat[n / 2], p75 = lat[(3 * n) / 4];
    uint64_t p99 = lat[(size_t)(0.99 * n)], mn = lat[0];
    printf("%s,%s,%zu,%zu,%llu,%llu,%llu,%llu,%llu\n", mode, pin, n, payload,
           (unsigned long long)p50, (unsigned long long)p25,
           (unsigned long long)p75, (unsigned long long)mn,
           (unsigned long long)p99);
    fflush(stdout);
}

/* ---- affinity ---- */
static void pin_to(int cpu) {
    if (cpu < 0) return;
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0)
        perror("sched_setaffinity");
}

/* ================= UDS ping-pong ================= */
struct uds_ctx {
    int fd;         /* this thread's end of the socketpair */
    int full;       /* 1 = uds_full (framed + getpid/gettid), 0 = uds_lean */
    size_t payload;
    int cpu;
};

static void write_all(int fd, const void *buf, size_t n) {
    const char *p = buf;
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w < 0) { if (errno == EINTR) continue; perror("write"); _exit(2); }
        p += w; n -= (size_t)w;
    }
}
static void read_all(int fd, void *buf, size_t n) {
    char *p = buf;
    while (n) {
        ssize_t r = read(fd, p, n);
        if (r <= 0) { if (r < 0 && errno == EINTR) continue; perror("read"); _exit(2); }
        p += r; n -= (size_t)r;
    }
}

/* server thread: echoes payload back, mirroring server-side framing when full */
static void *uds_server(void *arg) {
    struct uds_ctx *c = arg;
    pin_to(c->cpu);
    char *buf = malloc(c->payload + 16);
    for (;;) {
        if (c->full) {
            uint32_t len;
            char probe;
            ssize_t r0 = read(c->fd, &probe, 1);  /* tolerant: EOF => clean shutdown */
            if (r0 == 0) break;
            if (r0 < 0) { if (errno == EINTR) continue; perror("read"); _exit(2); }
            read_all(c->fd, ((char *)&len) + 1, 3);
            /* reconstruct len: for the bench payload is fixed, just drain it */
            read_all(c->fd, buf, c->payload);
            /* reply: 4B header + payload */
            uint32_t hdr = (uint32_t)c->payload;
            write_all(c->fd, &hdr, 4);
            write_all(c->fd, buf, c->payload);
        } else {
            ssize_t r = read(c->fd, buf, c->payload);
            if (r <= 0) break;                    /* client closed -> stop */
            write_all(c->fd, buf, (size_t)r);
        }
    }
    free(buf);
    return NULL;
}

static void run_uds(int full, const char *pin, int cpu_client, int cpu_server,
                    size_t iters, size_t warmup, size_t payload) {
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) { perror("socketpair"); _exit(2); }
    struct uds_ctx sctx = { .fd = sv[1], .full = full, .payload = payload, .cpu = cpu_server };
    pthread_t th;
    pthread_create(&th, NULL, uds_server, &sctx);
    pin_to(cpu_client);

    char *req = malloc(payload + 16), *resp = malloc(payload + 16);
    memset(req, 0xAB, payload);
    uint64_t *lat = malloc(iters * sizeof(uint64_t));

    for (size_t i = 0; i < warmup + iters; i++) {
        uint64_t t0 = now_ns();
        if (full) {
            /* exact ~7-syscall guest hop */
            (void)getpid();
            (void)syscall(SYS_gettid);
            char probe = 0;
            uint32_t hdr = (uint32_t)payload;
            write_all(sv[0], &probe, 1);          /* 1B (stands in for framing head) */
            write_all(sv[0], ((char *)&hdr) + 1, 3);
            write_all(sv[0], req, payload);
            uint32_t rhdr;
            char rprobe;
            read_all(sv[0], &rprobe, 1);
            read_all(sv[0], ((char *)&rhdr) + 1, 3);
            read_all(sv[0], resp, payload);
        } else {
            write_all(sv[0], req, payload);
            read_all(sv[0], resp, payload);
        }
        uint64_t dt = now_ns() - t0;
        if (i >= warmup) lat[i - warmup] = dt;
    }
    report(full ? "uds_full" : "uds_lean", pin, lat, iters, payload);

    close(sv[0]);                                  /* EOF => both servers break cleanly */
    pthread_join(th, NULL);
    free(req); free(resp); free(lat);
}

/* ================= futex ping-pong ================= */
/* two cachelines: turn==0 -> client's turn, turn==1 -> server's turn */
static int g_turn;                                 /* futex word */
static int g_stop;

static long futex(int *uaddr, int op, int val) {
    return syscall(SYS_futex, uaddr, op, val, NULL, NULL, 0);
}

struct futex_ctx { int cpu; };

static void *futex_server(void *arg) {
    struct futex_ctx *c = arg;
    pin_to(c->cpu);
    for (;;) {
        while (__atomic_load_n(&g_turn, __ATOMIC_ACQUIRE) != 1) {
            if (__atomic_load_n(&g_stop, __ATOMIC_ACQUIRE)) return NULL;
            futex(&g_turn, FUTEX_WAIT, 0);         /* wait while turn==0 */
        }
        /* "process request" == flip turn back to client and wake */
        __atomic_store_n(&g_turn, 0, __ATOMIC_RELEASE);
        futex(&g_turn, FUTEX_WAKE, 1);
    }
}

static void run_futex(const char *pin, int cpu_client, int cpu_server,
                      size_t iters, size_t warmup, size_t payload) {
    g_turn = 0; g_stop = 0;
    struct futex_ctx sctx = { .cpu = cpu_server };
    pthread_t th;
    pthread_create(&th, NULL, futex_server, &sctx);
    pin_to(cpu_client);
    uint64_t *lat = malloc(iters * sizeof(uint64_t));

    for (size_t i = 0; i < warmup + iters; i++) {
        uint64_t t0 = now_ns();
        /* hand to server: turn=1, wake */
        __atomic_store_n(&g_turn, 1, __ATOMIC_RELEASE);
        futex(&g_turn, FUTEX_WAKE, 1);
        /* wait for reply: turn back to 0 */
        while (__atomic_load_n(&g_turn, __ATOMIC_ACQUIRE) != 0)
            futex(&g_turn, FUTEX_WAIT, 1);
        uint64_t dt = now_ns() - t0;
        if (i >= warmup) lat[i - warmup] = dt;
    }
    report("futex", pin, lat, iters, payload);

    __atomic_store_n(&g_stop, 1, __ATOMIC_RELEASE);
    __atomic_store_n(&g_turn, 1, __ATOMIC_RELEASE);
    futex(&g_turn, FUTEX_WAKE, 1);
    pthread_join(th, NULL);
    free(lat);
}

int main(int argc, char **argv) {
    size_t iters   = argc > 1 ? strtoul(argv[1], NULL, 10) : 200000;
    size_t warmup  = argc > 2 ? strtoul(argv[2], NULL, 10) : 20000;
    size_t payload = argc > 3 ? strtoul(argv[3], NULL, 10) : 64;

    printf("mode,pin,iters,payload,p50_ns,p25_ns,p75_ns,min_ns,p99_ns\n");

    /* same-core: both threads pinned to CPU 0 (serialized handoff, like det-mode) */
    run_uds (1, "same", 0, 0, iters, warmup, payload);
    run_uds (0, "same", 0, 0, iters, warmup, payload);
    run_futex(  "same", 0, 0, iters, warmup, payload);

    /* cross-core: client CPU 0, server CPU 1 */
    run_uds (1, "cross", 0, 1, iters, warmup, payload);
    run_uds (0, "cross", 0, 1, iters, warmup, payload);
    run_futex(  "cross", 0, 1, iters, warmup, payload);

    return 0;
}
