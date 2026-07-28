#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static volatile long sum=0; static volatile sig_atomic_t cnt=0;
static void h(int s, siginfo_t*si, void*u){(void)s;(void)u; cnt++; sum+=si->si_value.sival_int;}
int main(void){struct sigaction sa; memset(&sa,0,sizeof sa);
  sa.sa_sigaction=h; sa.sa_flags=SA_SIGINFO; sigemptyset(&sa.sa_mask);
  sigaction(SIGRTMIN,&sa,NULL);
  sigset_t b,old,pend; sigemptyset(&b); sigaddset(&b,SIGRTMIN);
  sigprocmask(SIG_BLOCK,&b,&old);
  long exp=0; for(int v=1;v<=10;v++){union sigval sv; sv.sival_int=v; sigqueue(getpid(),SIGRTMIN,sv); exp+=v;}
  sigpending(&pend); int ps=sigismember(&pend,SIGRTMIN)?1:0;
  sigprocmask(SIG_UNBLOCK,&b,NULL);
  for(int i=0;i<1000000&&cnt<10;i++){}
  printf("cnt=%d sum=%ld exp=%ld ps=%d\n",cnt,sum,exp,ps); return 0;}
