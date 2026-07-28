#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
int main(void){
  pid_t p=fork();
  if(p==0){ printf("child\n"); _exit(3); }
  int st=0; waitpid(p,&st,0);
  printf("parent got %d\n", WIFEXITED(st)?WEXITSTATUS(st):-1);
  return 0;
}
