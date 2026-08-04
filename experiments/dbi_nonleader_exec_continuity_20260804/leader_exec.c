// CONTROL: leader (main thread) execve. No secondary threads. This is the
// ordinary fork/vfork+exec-equivalent leader exec that the corpus already
// covers and that reconnect_after_exec handles via caller==new_leader.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
static long gettid_(void){return syscall(SYS_gettid);}
static unsigned long long mono_ns(void){struct timespec ts;clock_gettime(CLOCK_MONOTONIC,&ts);return (unsigned long long)ts.tv_sec*1000000000ULL+ts.tv_nsec;}
int main(int argc,char**argv){
  setvbuf(stdout,NULL,_IONBF,0);
  if(argc>=2&&!strcmp(argv[1],"--post")){
    printf("POST tid=%ld pid=%d leader=%d t_post=%llu\n",gettid_(),getpid(),gettid_()==getpid(),mono_ns());
    return 0;
  }
  volatile unsigned long acc=0; for(int i=0;i<2000;i++)acc+=getpid();
  printf("PRE  tid=%ld pid=%d leader=%d t_pre=%llu\n",gettid_(),getpid(),gettid_()==getpid(),mono_ns());
  char*const a[]={argv[0],(char*)"--post",NULL}; char*const e[]={NULL};
  execve(argv[0],a,e); perror("execve"); return 97;
}
