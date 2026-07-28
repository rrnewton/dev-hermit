#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static volatile sig_atomic_t u1=0,u2=0,nested=0;
static void h(int s, siginfo_t*si, void*u){(void)si;(void)u;
  if(s==SIGUSR1){u1++; if(u1==1){nested=1; raise(SIGUSR2);}} else u2++;}
int main(void){struct sigaction sa; memset(&sa,0,sizeof sa);
  sa.sa_sigaction=h; sa.sa_flags=SA_SIGINFO; sigemptyset(&sa.sa_mask);
  sigaction(SIGUSR1,&sa,NULL); sigaction(SIGUSR2,&sa,NULL);
  for(int i=0;i<5;i++) raise(SIGUSR1);
  printf("u1=%d u2=%d nested=%d\n",u1,u2,nested); return 0;}
