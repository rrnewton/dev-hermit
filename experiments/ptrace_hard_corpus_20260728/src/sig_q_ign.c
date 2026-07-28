#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <unistd.h>
int main(void){signal(SIGRTMIN,SIG_IGN);
 union sigval sv;sv.sival_int=7;
 int r=sigqueue(getpid(),SIGRTMIN,sv);
 printf("sigqueue_ret=%d\n",r);return 0;}
