// pad_audit: black-box audit for syscalls that write uninitialized bytes into
// a guest struct.
//
//   usage: pad_audit [--trials N] [--verbose]
//
// Method, per syscall under test:
//   * poison the output buffer with 0xAA, invoke, snapshot image A
//   * PERTURB: issue k unrelated syscalls, k varying by trial
//   * poison the SAME buffer with 0x55, invoke again, snapshot image B
//   * a byte that differs between A and B was either not written at all
//     (it still holds its poison) or was written with indeterminate content
//
// The perturbation is load-bearing. Without it the two invocations run
// back-to-back through an identical supervisor code path, so uninitialized
// stack residue is byte-identical in A and B and the probe reports a false
// clean. Varying the intervening syscall work is what makes residue visible.
//
// Run the identical probe natively and under hermit. The discriminator is:
//
//   LEAK(byte b) := native writes b deterministically in every trial
//                   AND the implementation under test does not
//
// Bytes that legitimately change between two back-to-back calls (clock
// readings, uptime, free memory) are unstable natively too, so the rule
// filters them out by construction rather than by a hand-maintained allowlist.
//
// Output is one line per syscall:
//   <name> size=<n> unstable=<count> offsets=<list>
// plus a final UNSTABLE-SUMMARY line. Feed the native and hermit runs to
// audit_diff.py to apply the discriminator.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/sysinfo.h>
#include <sys/time.h>
#include <sys/times.h>
#include <sys/timex.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <sched.h>
#include <time.h>
#include <unistd.h>

enum { CAP = 512, MAX_TRIALS = 64 };

static int verbose = 0;

// Each probe fills `buf` by invoking one syscall. Returns 0 on success,
// -1 to skip (syscall unsupported / not applicable here).
typedef int (*probe_fn)(unsigned char *buf);
struct probe {
  const char *name;
  size_t size;      // bytes of `buf` the ABI defines as the output object
  probe_fn fn;
};

/* ---------- perturbation ------------------------------------------------ */

// Issue k unrelated syscalls so the next probe invocation reaches the
// determinizer with a different supervisor-stack history. Results are
// discarded; only the side effect on intervening code paths matters.
static void perturb(int k) {
  char sink[64];
  for (int i = 0; i < k; i++) {
    switch (i % 5) {
      case 0: (void)syscall(SYS_getpid); break;
      case 1: { struct timespec ts; (void)syscall(SYS_clock_gettime, CLOCK_MONOTONIC, &ts); break; }
      case 2: { int fd = (int)syscall(SYS_openat, AT_FDCWD, "/proc/self/stat", O_RDONLY);
                if (fd >= 0) { (void)syscall(SYS_read, fd, sink, sizeof sink); (void)syscall(SYS_close, fd); }
                break; }
      case 3: (void)syscall(SYS_getuid); break;
      case 4: { struct stat st; (void)syscall(SYS_fstat, 0, &st); break; }
    }
  }
}

/* ---------- individual probes ------------------------------------------- */

static int p_sysinfo(unsigned char *b)      { return syscall(SYS_sysinfo, b) < 0 ? -1 : 0; }
static int p_uname(unsigned char *b)        { return syscall(SYS_uname, b) < 0 ? -1 : 0; }
static int p_getrusage_self(unsigned char *b){ return syscall(SYS_getrusage, RUSAGE_SELF, b) < 0 ? -1 : 0; }
static int p_getrusage_child(unsigned char *b){ return syscall(SYS_getrusage, RUSAGE_CHILDREN, b) < 0 ? -1 : 0; }
static int p_times(unsigned char *b)        { return syscall(SYS_times, b) < 0 ? -1 : 0; }
static int p_stat_self(unsigned char *b)    { return syscall(SYS_newfstatat, AT_FDCWD, "/proc/self/exe", b, 0) < 0 ? -1 : 0; }
static int p_fstat_stdin(unsigned char *b)  { return syscall(SYS_fstat, 0, b) < 0 ? -1 : 0; }
static int p_statfs_root(unsigned char *b)  { return syscall(SYS_statfs, "/", b) < 0 ? -1 : 0; }
static int p_getrlimit(unsigned char *b)    { return syscall(SYS_prlimit64, 0, RLIMIT_NOFILE, NULL, b) < 0 ? -1 : 0; }
static int p_clock_gettime(unsigned char *b){ return syscall(SYS_clock_gettime, CLOCK_MONOTONIC, b) < 0 ? -1 : 0; }
static int p_clock_getres(unsigned char *b) { return syscall(SYS_clock_getres, CLOCK_MONOTONIC, b) < 0 ? -1 : 0; }
static int p_gettimeofday(unsigned char *b) { return syscall(SYS_gettimeofday, b, NULL) < 0 ? -1 : 0; }
static int p_getitimer(unsigned char *b)    { return syscall(SYS_getitimer, ITIMER_REAL, b) < 0 ? -1 : 0; }
static int p_adjtimex(unsigned char *b) {
  // Read-only query: modes == 0. The kernel fills the whole struct timex.
  memset(b, 0, sizeof(struct timex));
  int r = syscall(SYS_adjtimex, b);
  return r < 0 ? -1 : 0;
}
static int p_sigaction_old(unsigned char *b) {
  // rt_sigaction with a NULL new-action reads the current handler into oldact.
  return syscall(SYS_rt_sigaction, SIGUSR1, NULL, b, 8) < 0 ? -1 : 0;
}
static int p_sigprocmask_old(unsigned char *b) {
  return syscall(SYS_rt_sigprocmask, SIG_BLOCK, NULL, b, 8) < 0 ? -1 : 0;
}
static int p_getcpu(unsigned char *b)       { return syscall(SYS_getcpu, b, b + 4, NULL) < 0 ? -1 : 0; }
static int p_sched_getparam(unsigned char *b){ return syscall(SYS_sched_getparam, 0, b) < 0 ? -1 : 0; }
static int p_getresuid(unsigned char *b)    { return syscall(SYS_getresuid, b, b + 4, b + 8) < 0 ? -1 : 0; }
static int p_getresgid(unsigned char *b)    { return syscall(SYS_getresgid, b, b + 4, b + 8) < 0 ? -1 : 0; }
static int p_statx_self(unsigned char *b)   { return syscall(SYS_statx, AT_FDCWD, "/proc/self/exe", 0, 0x7ff, b) < 0 ? -1 : 0; }
static int p_waitid_nochild(unsigned char *b) {
  // Fork a child that exits immediately, then waitid into `b`. siginfo_t has a
  // large union plus explicit padding, so it is a prime candidate.
  pid_t pid = fork();
  if (pid == 0) { _exit(7); }
  if (pid < 0) { return -1; }
  int r = syscall(SYS_waitid, P_PID, pid, b, WEXITED, NULL);
  return r < 0 ? -1 : 0;
}
static int p_timer_gettime(unsigned char *b) {
  static timer_t tid;
  static int made = 0;
  if (!made) {
    struct sigevent sev; memset(&sev, 0, sizeof sev);
    sev.sigev_notify = SIGEV_NONE;
    if (syscall(SYS_timer_create, CLOCK_MONOTONIC, &sev, &tid) < 0) { return -1; }
    made = 1;
  }
  return syscall(SYS_timer_gettime, tid, b) < 0 ? -1 : 0;
}

static const struct probe PROBES[] = {
  {"sysinfo",           sizeof(struct sysinfo),   p_sysinfo},
  {"uname",             sizeof(struct utsname),   p_uname},
  {"getrusage_self",    sizeof(struct rusage),    p_getrusage_self},
  {"getrusage_child",   sizeof(struct rusage),    p_getrusage_child},
  {"times",             sizeof(struct tms),       p_times},
  {"newfstatat",        sizeof(struct stat),      p_stat_self},
  {"fstat",             sizeof(struct stat),      p_fstat_stdin},
  {"statfs",            sizeof(struct statfs),    p_statfs_root},
  {"prlimit64",         sizeof(struct rlimit),    p_getrlimit},
  {"clock_gettime",     sizeof(struct timespec),  p_clock_gettime},
  {"clock_getres",      sizeof(struct timespec),  p_clock_getres},
  {"gettimeofday",      sizeof(struct timeval),   p_gettimeofday},
  {"getitimer",         sizeof(struct itimerval), p_getitimer},
  {"timer_gettime",     sizeof(struct itimerspec),p_timer_gettime},
  {"adjtimex",          sizeof(struct timex),     p_adjtimex},
  {"rt_sigaction_old",  32,                       p_sigaction_old},
  {"rt_sigprocmask_old",8,                        p_sigprocmask_old},
  {"getcpu",            8,                        p_getcpu},
  {"sched_getparam",    4,                        p_sched_getparam},
  {"getresuid",         12,                       p_getresuid},
  {"getresgid",         12,                       p_getresgid},
  {"statx",             256,                      p_statx_self},
  {"waitid",            128,                      p_waitid_nochild},
};

int main(int argc, char **argv) {
  int trials = 8;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--trials") && i + 1 < argc) trials = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--verbose")) verbose = 1;
  }
  if (trials < 1 || trials > MAX_TRIALS) { fprintf(stderr, "bad --trials\n"); return 2; }

  int total_unstable_probes = 0;

  for (size_t p = 0; p < sizeof PROBES / sizeof PROBES[0]; p++) {
    const struct probe *pr = &PROBES[p];
    if (pr->size > CAP) { printf("%s SKIP size=%zu exceeds CAP\n", pr->name, pr->size); continue; }

    unsigned char unstable[CAP];
    memset(unstable, 0, sizeof unstable);
    int skipped = 0;

    for (int t = 0; t < trials && !skipped; t++) {
      unsigned char a[CAP], b[CAP];
      static const unsigned char POISON[2] = {0xAA, 0x55};

      memset(a, POISON[0], CAP);
      if (pr->fn(a) != 0) { skipped = 1; break; }
      perturb(t);                      // varying supervisor-stack history
      memset(b, POISON[1], CAP);
      if (pr->fn(b) != 0) { skipped = 1; break; }

      for (size_t i = 0; i < pr->size; i++) {
        if (a[i] != b[i]) unstable[i] = 1;
      }
    }

    if (skipped) { printf("%s SKIP errno=%d\n", pr->name, errno); continue; }

    size_t n = 0;
    for (size_t i = 0; i < pr->size; i++) n += unstable[i];
    printf("%s size=%zu unstable=%zu offsets=", pr->name, pr->size, n);
    if (n == 0) {
      printf("-");
    } else {
      // print as compact ranges
      size_t i = 0;
      int first = 1;
      while (i < pr->size) {
        if (!unstable[i]) { i++; continue; }
        size_t j = i;
        while (j + 1 < pr->size && unstable[j + 1]) j++;
        printf("%s%zu-%zu", first ? "" : ",", i, j + 1);
        first = 0;
        i = j + 1;
      }
      total_unstable_probes++;
    }
    printf("\n");
  }

  printf("UNSTABLE-SUMMARY probes_with_unstable_bytes=%d trials=%d\n",
         total_unstable_probes, trials);
  return 0;
}
