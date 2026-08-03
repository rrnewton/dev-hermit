#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <fcntl.h>
#include <sched.h>
#include <ucontext.h>
#include <sys/personality.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <linux/perf_event.h>

/* In-guest RCB-preemption determinism spike.
 * Q: if the GUEST fields its OWN PMU-overflow signal (no external ptrace tracer),
 *    does the preemption land at a DETERMINISTIC point across identical runs,
 *    or does it scatter (the DBI in-process re-entrancy / skid wall)?
 * Host forces precise_ip=0 (max_precise=0, no PEBS) => this is the SKID-PRONE path,
 * the pessimistic case. If even this lands deterministically, YES; if it scatters, NO. */

static int g_fd = -1;
static volatile unsigned long g_iters = 0;     /* workload progress, incremented per iter */
static volatile unsigned long long g_ctr_at_sig = 0; /* actual RCB count at delivery */
static volatile unsigned long g_iter_at_sig = 0;
static volatile unsigned long long g_rip_at_sig = 0;
static volatile int g_fired = 0;

static long pe_open(struct perf_event_attr*a,pid_t p,int c,int g,unsigned long f){
  return syscall(SYS_perf_event_open,a,p,c,g,f);
}

static void on_overflow(int sig, siginfo_t *si, void *uc){
  if (g_fired) return;
  g_fired = 1;
  ioctl(g_fd, PERF_EVENT_IOC_DISABLE, 0);           /* AS-safe */
  unsigned long long v=0; read(g_fd,&v,sizeof(v));  /* AS-safe */
  g_ctr_at_sig = v;
  g_iter_at_sig = g_iters;
  ucontext_t *c = (ucontext_t*)uc;
  g_rip_at_sig = (unsigned long long)c->uc_mcontext.gregs[REG_RIP];
  (void)sig;(void)si;
}

/* deterministic workload: fixed conditional-branch count per iteration */
__attribute__((noinline)) static long workload(long N){
  volatile long acc=0;
  for (long i=0;i<N;i++){
    g_iters = i;                 /* progress marker read by handler */
    if (i & 1) acc += i; else acc -= i;   /* conditional branch */
    acc ^= (acc<<1);
  }
  return acc;
}

int main(int argc, char**argv){
  /* re-exec once with ASLR off so RIP is comparable across runs */
  if (!getenv("SPIKE_NORAND")){
    personality(ADDR_NO_RANDOMIZE);
    setenv("SPIKE_NORAND","1",1);
    execv("/proc/self/exe", argv);
    perror("execv"); return 3;
  }
  long N     = argc>1?atol(argv[1]):5000000;
  long TGT   = argc>2?atol(argv[2]):1000000; /* overflow after ~TGT retired branches */

  cpu_set_t s; CPU_ZERO(&s); CPU_SET(3,&s); sched_setaffinity(0,sizeof(s),&s);

  struct sigaction sa; memset(&sa,0,sizeof(sa));
  sa.sa_sigaction=on_overflow; sa.sa_flags=SA_SIGINFO|SA_RESTART;
  sigemptyset(&sa.sa_mask); sigaction(SIGRTMIN,&sa,NULL);

  struct perf_event_attr a; memset(&a,0,sizeof(a));
  a.type=PERF_TYPE_HARDWARE; a.size=sizeof(a);
  a.config=PERF_COUNT_HW_BRANCH_INSTRUCTIONS; /* retired branch instructions */
  a.sample_period=TGT;         /* overflow after TGT events */
  a.disabled=1; a.pinned=1;    /* pinned=1: same as reverie perf.rs:210 */
  a.exclude_kernel=1; a.exclude_hv=1;
  a.wakeup_events=1;
  a.precise_ip=0;              /* forced: host max_precise=0 (no PEBS) */
  long fd=pe_open(&a,0,-1,-1,0);
  if(fd<0){ perror("perf_event_open"); return 2; }
  g_fd=(int)fd;

  fcntl(fd,F_SETFL,O_ASYNC);
  fcntl(fd,F_SETSIG,SIGRTMIN);
  struct f_owner_ex ow={F_OWNER_TID, (int)syscall(SYS_gettid)};
  fcntl(fd,F_SETOWN_EX,&ow);

  ioctl(fd,PERF_EVENT_IOC_RESET,0);
  ioctl(fd,PERF_EVENT_IOC_ENABLE,0);
  long r=workload(N);

  /* result line: fired iter_at_sig ctr_at_sig rip_at_sig  (workload result to defeat DCE) */
  printf("fired=%d iter_at_sig=%lu ctr_at_sig=%llu rip_at_sig=0x%llx r=%ld\n",
         g_fired, g_iter_at_sig, g_ctr_at_sig, g_rip_at_sig, r);
  return 0;
}
