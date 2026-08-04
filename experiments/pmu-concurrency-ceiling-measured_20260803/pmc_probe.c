/* Direct per-core pinned-PMC capacity probe.
 * Opens N pinned per-task (pid=0,cpu=-1) HW_BRANCH_INSTRUCTIONS events,
 * pins self to one core, runs a loop, then reports for each event whether
 * time_enabled==time_running (fully resident) vs descheduled/multiplexed.
 * The count of fully-resident events == usable simultaneous pinned PMCs/core.
 */
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

static long perf_open(struct perf_event_attr *a,pid_t pid,int cpu,int grp,unsigned long fl){
  return syscall(__NR_perf_event_open,a,pid,cpu,grp,fl);
}
int main(int argc,char**argv){
  int N = argc>1?atoi(argv[1]):8;
  cpu_set_t set; CPU_ZERO(&set); CPU_SET(3,&set);
  if(sched_setaffinity(0,sizeof(set),&set)) perror("affinity");
  int fds[64]; int opened=0;
  for(int i=0;i<N;i++){
    struct perf_event_attr a; memset(&a,0,sizeof(a));
    a.type=PERF_TYPE_HARDWARE; a.size=sizeof(a);
    a.config=PERF_COUNT_HW_BRANCH_INSTRUCTIONS;
    a.disabled=1; a.pinned=1; a.exclude_kernel=1; a.exclude_hv=1;
    a.read_format=PERF_FORMAT_TOTAL_TIME_ENABLED|PERF_FORMAT_TOTAL_TIME_RUNNING;
    long fd=perf_open(&a,0,-1,-1,0);
    if(fd<0){ printf("event %d: perf_event_open FAILED errno=%d (%s)\n",i,errno,strerror(errno)); break; }
    fds[opened++]=(int)fd;
  }
  for(int i=0;i<opened;i++){ ioctl(fds[i],PERF_EVENT_IOC_RESET,0); ioctl(fds[i],PERF_EVENT_IOC_ENABLE,0);}
  volatile unsigned long x=0; for(unsigned long i=0;i<200000000UL;i++) x+=i;
  for(int i=0;i<opened;i++) ioctl(fds[i],PERF_EVENT_IOC_DISABLE,0);
  int resident=0;
  for(int i=0;i<opened;i++){
    unsigned long v[3]; /* count, enabled, running */
    if(read(fds[i],v,sizeof(v))!=sizeof(v)){ printf("event %d: SHORT READ (descheduled/error state)\n",i); continue;}
    int full = (v[1]==v[2] && v[2]>0);
    if(full) resident++;
    printf("event %d: count=%lu enabled=%lu running=%lu %s\n",i,v[0],v[1],v[2], full?"RESIDENT":"MULTIPLEXED/OFF");
  }
  printf("\nRESULT: %d of %d pinned per-task PMCs fully resident on one core (=usable simultaneous PMCs)\n",resident,opened);
  return 0;
}
