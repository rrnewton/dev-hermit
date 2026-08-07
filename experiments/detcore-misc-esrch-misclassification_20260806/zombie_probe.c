/* Verifies the claim that kill(pid,0) CANNOT be the discriminator because an
 * unreaped zombie still answers it, while /proc/<pid>/stat reports 'Z'. */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
static int alive_by_kill(pid_t p){ return kill(p,0)==0 || errno==EPERM; }
static char proc_state(pid_t p){ char pa[64],b[512];
  snprintf(pa,sizeof pa,"/proc/%d/stat",(int)p); FILE*f=fopen(pa,"r");
  if(!f) return '?';
  if(!fgets(b,sizeof b,f)){ fclose(f); return '?'; }
  fclose(f);
  char*c=strrchr(b,')'); return c?c[2]:'?'; }
int main(int argc,char**argv){
  int trials = argc>1?atoi(argv[1]):20;
  printf("case,alive_by_kill,proc_state,kill_would_misclassify\n");
  for(int i=0;i<trials;++i){
    pid_t c=fork();
    if(c==0) _exit(0);
    usleep(20000);                    /* child has exited; NOT reaped => zombie */
    int a=alive_by_kill(c); char s=proc_state(c);
    /* ground truth: DEAD. kill misclassifies iff it says alive. */
    printf("unreaped_zombie,%d,%c,%d\n", a, s, a?1:0);
    int st; waitpid(c,&st,0);
  }
  return 0;
}
