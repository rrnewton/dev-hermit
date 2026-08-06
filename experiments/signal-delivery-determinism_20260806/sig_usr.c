/* Self-raised SIGUSR1/SIGUSR2 interleaved with work: strict ordering test. */
#define _GNU_SOURCE
#include <stdio.h>
#include <signal.h>
#include <unistd.h>
static void h(int s){printf("SIG%s\n", s==SIGUSR1?"USR1":"USR2"); fflush(stdout);}
int main(void){
  struct sigaction sa={0}; sa.sa_handler=h;
  sigaction(SIGUSR1,&sa,NULL); sigaction(SIGUSR2,&sa,NULL);
  for(int i=0;i<5;i++){ raise(i%2?SIGUSR2:SIGUSR1); printf("work %d\n",i); fflush(stdout);}
  return 0;}
