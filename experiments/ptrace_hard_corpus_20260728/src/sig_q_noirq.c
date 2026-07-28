#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
int main(void){
 sigset_t b;sigemptyset(&b);sigaddset(&b,SIGRTMIN);
 sigprocmask(SIG_BLOCK,&b,NULL);       // block, never unblock
 union sigval sv;sv.sival_int=7;
 int r=sigqueue(getpid(),SIGRTMIN,sv);  // rt_sigqueueinfo syscall
 sigset_t pend;sigpending(&pend);
 printf("sigqueue_ret=%d pending=%d\n",r,sigismember(&pend,SIGRTMIN)?1:0);
 return 0;}
