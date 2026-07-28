#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static volatile sig_atomic_t cnt=0;
static void h(int s){(void)s;cnt++;}
int main(void){signal(SIGUSR1,h);
 sigset_t b,old;sigemptyset(&b);sigaddset(&b,SIGUSR1);
 sigprocmask(SIG_BLOCK,&b,&old);
 kill(getpid(),SIGUSR1);
 sigprocmask(SIG_UNBLOCK,&b,NULL);
 for(int i=0;i<1000000&&cnt<1;i++){}
 printf("cnt=%d\n",cnt);return 0;}
