/* SIGALRM via setitimer. The loop must be long enough in VIRTUAL time for the
   timer to fire; it does periodic getpid() so virtual time advances via syscalls
   as well as RCBs. Prints delivery order: (tick, iteration-at-delivery). */
#define _GNU_SOURCE
#include <stdio.h>
#include <signal.h>
#include <sys/time.h>
#include <unistd.h>
static volatile sig_atomic_t ticks=0; static volatile long witness=0;
static void h(int s){(void)s; printf("ALRM tick=%d at_iter=%ld\n",++ticks,witness); fflush(stdout);}
int main(void){
  struct sigaction sa={0}; sa.sa_handler=h; sa.sa_flags=SA_RESTART; sigaction(SIGALRM,&sa,NULL);
  struct itimerval t={{0,1000},{0,1000}};  /* 1ms repeating */
  setitimer(ITIMER_REAL,&t,NULL);
  for(witness=0; witness<200000 && ticks<5; witness++) (void)getpid();
  struct itimerval off={{0,0},{0,0}}; setitimer(ITIMER_REAL,&off,NULL);
  printf("final ticks=%d\n",ticks); return 0;}
