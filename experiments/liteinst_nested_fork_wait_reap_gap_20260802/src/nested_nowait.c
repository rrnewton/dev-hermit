#define _GNU_SOURCE
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>
int main(void){
  pid_t c=fork();
  if(c<0)return 1;
  if(c==0){ pid_t g=fork(); if(g<0)_exit(3); if(g==0){_exit(0);} _exit(0);} // child1 does NOT wait for g
  int st=0; waitpid(c,&st,0);
  if(WIFEXITED(st)&&WEXITSTATUS(st)==0) puts("nowait-ok");
  return 0;
}
