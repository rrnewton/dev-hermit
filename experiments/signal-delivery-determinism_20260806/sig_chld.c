/* SIGCHLD ordering against explicit waitpid: the handler/wait interleaving must
   be identical run to run. */
#define _GNU_SOURCE
#include <stdio.h>
#include <signal.h>
#include <unistd.h>
#include <sys/wait.h>
static int n=0;
static void h(int s){(void)s; printf("SIGCHLD #%d\n",++n); fflush(stdout);}
int main(void){
  struct sigaction sa={0}; sa.sa_handler=h; sa.sa_flags=SA_RESTART; sigaction(SIGCHLD,&sa,NULL);
  for(int i=0;i<3;i++){ pid_t p=fork(); if(p==0){_exit(i);} printf("forked child %d\n",i); fflush(stdout);}
  int st,c=0; while(wait(&st)>0){ printf("reaped exit=%d\n", WEXITSTATUS(st)); fflush(stdout); c++; }
  printf("handlers=%d reaped=%d\n",n,c); return 0;}
