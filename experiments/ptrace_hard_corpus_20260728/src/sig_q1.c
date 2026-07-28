#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static volatile long sum=0; static volatile sig_atomic_t cnt=0;
static void h(int s,siginfo_t*si,void*u){(void)s;(void)u;cnt++;sum+=si->si_value.sival_int;}
int main(void){struct sigaction sa;memset(&sa,0,sizeof sa);
 sa.sa_sigaction=h;sa.sa_flags=SA_SIGINFO;sigemptyset(&sa.sa_mask);
 sigaction(SIGRTMIN,&sa,NULL);
 union sigval sv;sv.sival_int=7;sigqueue(getpid(),SIGRTMIN,sv);
 for(int i=0;i<1000000&&cnt<1;i++){}
 printf("cnt=%d sum=%ld\n",cnt,sum);return 0;}
