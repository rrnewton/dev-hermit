#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sched.h>
#include <linux/perf_event.h>
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <errno.h>

static long pe_open(struct perf_event_attr *a,pid_t pid,int cpu,int grp,unsigned long f){
  return syscall(SYS_perf_event_open,a,pid,cpu,grp,f);
}
int main(){
  // pin self to CPU 3 (same box used for S1)
  cpu_set_t s; CPU_ZERO(&s); CPU_SET(3,&s);
  if(sched_setaffinity(0,sizeof(s),&s)) perror("affinity");
  int fds[64]; int n=0;
  for(int i=0;i<64;i++){
    struct perf_event_attr a; memset(&a,0,sizeof(a));
    a.type=PERF_TYPE_HARDWARE; a.size=sizeof(a);
    a.config=PERF_COUNT_HW_BRANCH_INSTRUCTIONS; // RCB-class hardware event
    a.disabled=1; a.pinned=1; // <- same as reverie perf.rs:210
    a.exclude_kernel=1; a.exclude_hv=1;
    a.read_format=PERF_FORMAT_TOTAL_TIME_ENABLED|PERF_FORMAT_TOTAL_TIME_RUNNING;
    long fd=pe_open(&a,0,-1,-1,0); // per-task (pid=self), any cpu — as reverie does
    if(fd<0){ printf("open #%d FAILED errno=%d (%s)\n",i+1,errno,strerror(errno)); break; }
    fds[n++]=(int)fd;
    ioctl(fd,PERF_EVENT_IOC_ENABLE,0);
  }
  printf("opened %d pinned per-task branch counters\n",n);
  // do a little work so events schedule, then check ENABLED vs RUNNING (multiplex/deschedule detector)
  volatile long acc=0; for(long i=0;i<20000000;i++) acc+=i&1;
  int active=0,descheduled=0;
  for(int i=0;i<n;i++){
    unsigned long long v[3]; // value, enabled, running
    ssize_t r=read(fds[i],v,sizeof(v));
    if(r<(ssize_t)sizeof(v)){ printf("ctr[%d] short read (ERROR state)\n",i); descheduled++; continue; }
    if(v[2]==0){ printf("ctr[%d] running=0 (never scheduled)\n",i); descheduled++; }
    else if(v[1]!=v[2]){ printf("ctr[%d] enabled=%llu running=%llu MULTIPLEXED/descheduled\n",i,v[1],v[2]); descheduled++; }
    else active++;
  }
  printf("RESULT: %d counters ran full-time (enabled==running); %d descheduled/multiplexed\n",active,descheduled);
  printf("=> K (max simultaneously-active pinned generic counters on one core) ~= %d\n",active);
  return 0;
}
