// S1 COST measurement: what does in-guest tool-RCB bracketing cost?
//
// Isolates the ONE quantity the coordinator asked for: the cost of reading the
// Detcore RCB clock counter (event AMD_RCB_EVENT = 0x5100d1, reverie
// timer.rs:64) on a LIVE, self-scheduled thread — which is what an in-guest
// backend must do to bracket a tool callback (read at entry, read before
// return, deduct the delta). This is a property of (read-primitive x kernel x
// PMU), independent of the liteinst hook's own work, so it is measured in
// isolation exactly as the S1(b) trap-microbench isolated axis (b).
//
// Two read primitives, matching the design doc:
//   (a) read() syscall  -- reverie's ctr_value (perf.rs:337-360); the path
//       reverie's fast-loop FALLS BACK to when index!=0 (perf.rs:420-430),
//       i.e. exactly the in-guest live-counter case.
//   (b) rdpmc           -- the in-guest-native primitive reverie does NOT
//       implement anywhere; valid precisely in the case ptrace avoided
//       (reading your own currently-scheduled counter on the core you run on).
//
// Plus the ptrace tracer per-stop bookkeeping baseline: the reset dance
// (reset + set_period + enable ioctls, perf.rs:293-310 / timer.rs:651-653).
// The ptrace read_clock reads a STOPPED tracee (index==0) => a bare mmap
// offset load, ~free; measured here as read_offset for completeness.
//
// Attrs mirror reverie perf.rs Builder::create (perf.rs:200-221) EXACTLY:
// PERF_TYPE_RAW, exclude_kernel/guest/hv, pinned=1, per-tid (pid=0), cpu=-1.
// pinned=1 => a deschedule EOFs read() / diverges running!=enabled: we DETECT
// and abort with a clear message rather than silently corrupt a number.

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <linux/perf_event.h>

#define AMD_RCB_EVENT 0x5100d1ULL   // reverie-ptrace/src/timer.rs:64

static long perf_event_open(struct perf_event_attr *a, pid_t pid, int cpu,
                            int grp, unsigned long fl) {
    return syscall(SYS_perf_event_open, a, pid, cpu, grp, fl);
}

static inline uint64_t now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

// Open a counting RCB counter mirroring reverie's clock counter (period 0,
// fast_reads => mmap page). Returns fd; sets *pg to the mmap page.
static int open_rcb(struct perf_event_mmap_page **pg) {
    struct perf_event_attr a;
    memset(&a, 0, sizeof a);
    a.size = sizeof a;
    a.type = PERF_TYPE_RAW;
    a.config = AMD_RCB_EVENT;
    a.sample_period = 0;      // counting (reverie clock counter: period 0)
    a.disabled = 1;           // enable explicitly below
    a.exclude_kernel = 1;
    a.exclude_guest = 1;
    a.exclude_hv = 1;
    a.pinned = 1;             // reverie sets pinned=1
    long fd = perf_event_open(&a, 0, -1, -1, PERF_FLAG_FD_CLOEXEC);
    if (fd < 0) { perror("perf_event_open"); exit(2); }
    void *m = mmap(NULL, sysconf(_SC_PAGESIZE), PROT_READ, MAP_SHARED,
                   (int)fd, 0);
    if (m == MAP_FAILED) { perror("mmap"); exit(2); }
    *pg = (struct perf_event_mmap_page *)m;
    if (ioctl((int)fd, PERF_EVENT_IOC_ENABLE, 0) < 0) {
        perror("ioctl ENABLE"); exit(2);
    }
    return (int)fd;
}

// (a) read() syscall read -- reverie ctr_value (perf.rs:337-360).
static inline uint64_t read_syscall(int fd) {
    uint64_t v;
    ssize_t r = read(fd, &v, sizeof v);
    if (r == 0) { fprintf(stderr, "FATAL: pinned perf event descheduled (EOF) — box not quiet enough\n"); exit(3); }
    if (r != (ssize_t)sizeof v) { perror("read"); exit(3); }
    return v;
}

static inline uint64_t rdpmc(uint32_t idx) {
    uint32_t lo, hi;
    __asm__ volatile("rdpmc" : "=a"(lo), "=d"(hi) : "c"(idx));
    return ((uint64_t)hi << 32) | lo;
}

// (b) rdpmc read -- the index!=0 LIVE path reverie omits. Standard perf
// seqlock protocol: value = offset + sign_extend(rdpmc(index-1), pmc_width).
static inline uint64_t read_rdpmc(struct perf_event_mmap_page *pc) {
    uint64_t count;
    uint32_t seq, idx, width;
    int64_t off;
    do {
        seq = pc->lock;
        __sync_synchronize();
        idx   = pc->index;
        off   = pc->offset;
        width = pc->pmc_width;
        if (idx == 0) {
            // Descheduled: offset already holds the frozen value (the ptrace
            // stopped-tracee case). No rdpmc.
            count = (uint64_t)off;
        } else {
            uint64_t raw = rdpmc(idx - 1);
            raw <<= (64 - width);              // sign-extend to counter width
            count = (uint64_t)(off + ((int64_t)raw >> (64 - width)));
        }
        __sync_synchronize();
    } while (pc->lock != seq);
    return count;
}

// Median of a small uint64 array (sorts in place).
static int cmp_u64(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}
static uint64_t median(uint64_t *v, int n) {
    qsort(v, n, sizeof(uint64_t), cmp_u64);
    return (n & 1) ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

// Time N reads of a primitive; return total ns. `which`: 0=syscall,1=rdpmc,
// 2=offset-only (ptrace stopped-tracee read_clock). volatile sink prevents
// the loop being optimized away.
static volatile uint64_t g_sink;
static uint64_t time_reads(int which, int fd, struct perf_event_mmap_page *pc,
                           long N) {
    uint64_t t0 = now_ns();
    for (long i = 0; i < N; i++) {
        switch (which) {
            case 0: g_sink = read_syscall(fd); break;
            case 1: g_sink = read_rdpmc(pc); break;
            case 2: g_sink = (uint64_t)pc->offset; break; // bare offset load
        }
    }
    uint64_t t1 = now_ns();
    return t1 - t0;
}

// Two-point slope ns/read, median over `reps` (2 warmups discarded).
static double slope_ns(int which, int fd, struct perf_event_mmap_page *pc,
                       long Na, long Nb, int reps) {
    int m = reps;
    uint64_t *ta = malloc(sizeof(uint64_t) * m);
    uint64_t *tb = malloc(sizeof(uint64_t) * m);
    for (int r = 0; r < m + 2; r++) {
        uint64_t a = time_reads(which, fd, pc, Na);
        uint64_t b = time_reads(which, fd, pc, Nb);
        if (r >= 2) { ta[r - 2] = a; tb[r - 2] = b; }
    }
    double ma = (double)median(ta, m), mb = (double)median(tb, m);
    free(ta); free(tb);
    return (mb - ma) / (double)(Nb - Na);
}

// RCB self-cost: how many 0x5100d1 conditional branches one primitive call
// consumes. Bracket a loop of N primitive calls with a SECOND RCB counter
// (read via syscall, outside the timed region), subtract an empty-loop
// baseline. Returns branches/call.
static double rcb_self_cost(int which, int fd, struct perf_event_mmap_page *pc,
                            int fd2, long N) {
    // empty-loop baseline
    uint64_t b0 = read_syscall(fd2);
    for (long i = 0; i < N; i++) { g_sink = i; }
    uint64_t b1 = read_syscall(fd2);
    double base = (double)(b1 - b0);
    // primitive loop
    uint64_t p0 = read_syscall(fd2);
    for (long i = 0; i < N; i++) {
        switch (which) {
            case 0: g_sink = read_syscall(fd); break;
            case 1: g_sink = read_rdpmc(pc); break;
            case 2: g_sink = (uint64_t)pc->offset; break;
        }
    }
    uint64_t p1 = read_syscall(fd2);
    double prim = (double)(p1 - p0);
    return (prim - base) / (double)N;
}

int main(int argc, char **argv) {
    long N   = (argc > 1) ? atol(argv[1]) : 200000;   // large-N point
    long Na  = (argc > 2) ? atol(argv[2]) : 20000;    // small-N point
    int reps = (argc > 3) ? atoi(argv[3]) : 15;

    struct perf_event_mmap_page *pc = NULL, *pc2 = NULL;
    int fd  = open_rcb(&pc);
    int fd2 = open_rcb(&pc2);   // second counter for RCB self-cost bracketing

    int rdpmc_ok = pc->cap_user_rdpmc;
    fprintf(stderr, "cap_user_rdpmc=%d index=%u pmc_width=%u offset=%lld\n",
            rdpmc_ok, pc->index, pc->pmc_width, (long long)pc->offset);

    // sanity: both primitives agree (only if rdpmc permitted & counter live)
    uint64_t vs = read_syscall(fd);
    if (rdpmc_ok) {
        uint64_t vr = read_rdpmc(pc);
        fprintf(stderr, "sanity read: syscall=%lu rdpmc=%lu (delta=%ld)\n",
                vs, vr, (long)(vr - vs));
    }

    printf("primitive,ns_per_read,rcb_per_read,Na,Nb,reps\n");

    double ns_sys = slope_ns(0, fd, pc, Na, N, reps);
    double rcb_sys = rcb_self_cost(0, fd, pc, fd2, Na);
    printf("read_syscall,%.2f,%.2f,%ld,%ld,%d\n", ns_sys, rcb_sys, Na, N, reps);

    if (rdpmc_ok) {
        double ns_rd = slope_ns(1, fd, pc, Na, N, reps);
        double rcb_rd = rcb_self_cost(1, fd, pc, fd2, Na);
        printf("read_rdpmc,%.2f,%.2f,%ld,%ld,%d\n", ns_rd, rcb_rd, Na, N, reps);
    } else {
        printf("read_rdpmc,NA_cap_user_rdpmc=0,NA,%ld,%ld,%d\n", Na, N, reps);
    }

    double ns_off = slope_ns(2, fd, pc, Na, N, reps);
    double rcb_off = rcb_self_cost(2, fd, pc, fd2, Na);
    printf("read_offset_stopped,%.2f,%.2f,%ld,%ld,%d\n", ns_off, rcb_off, Na, N, reps);

    // ptrace reset dance: reset + set_period + enable, per stop (timer.rs:651-653)
    {
        int m = reps;
        uint64_t *tt = malloc(sizeof(uint64_t) * m);
        long RN = 5000;
        for (int r = 0; r < m + 2; r++) {
            uint64_t t0 = now_ns();
            for (long i = 0; i < RN; i++) {
                ioctl(fd, PERF_EVENT_IOC_RESET, 0);
                uint64_t period = 1ull << 60; // DISABLE_SAMPLE_PERIOD analog
                ioctl(fd, PERF_EVENT_IOC_PERIOD, &period);
                ioctl(fd, PERF_EVENT_IOC_ENABLE, 0);
            }
            uint64_t t1 = now_ns();
            if (r >= 2) tt[r - 2] = (t1 - t0);
        }
        double ns_dance = (double)median(tt, m) / (double)RN;
        free(tt);
        printf("ptrace_reset_dance,%.2f,NA,%ld,%ld,%d\n", ns_dance, RN, RN, reps);
    }

    close(fd); close(fd2);
    return 0;
}
