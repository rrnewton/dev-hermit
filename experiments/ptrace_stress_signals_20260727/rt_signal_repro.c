#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
static volatile sig_atomic_t c=0; static void h(int s){c++;}
int main(void){ struct sigaction sa; memset(&sa,0,sizeof sa); sa.sa_handler=h; sigemptyset(&sa.sa_mask);
  sigaction(SIGRTMIN,&sa,NULL); raise(SIGRTMIN); printf("A c=%d\n",(int)c); return 0; }
