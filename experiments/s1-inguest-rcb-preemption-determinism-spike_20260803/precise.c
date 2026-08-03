#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <linux/perf_event.h>
#include <sys/syscall.h>
static long pe(struct perf_event_attr*a){return syscall(SYS_perf_event_open,a,0,-1,-1,0);}
int main(){
  for(int p=0;p<=3;p++){
    struct perf_event_attr a; memset(&a,0,sizeof(a));
    a.type=PERF_TYPE_HARDWARE; a.size=sizeof(a);
    a.config=PERF_COUNT_HW_BRANCH_INSTRUCTIONS;
    a.sample_period=100000; a.disabled=1; a.pinned=1;
    a.exclude_kernel=1; a.exclude_hv=1; a.precise_ip=p;
    long fd=pe(&a);
    if(fd<0) printf("precise_ip=%d => FAIL errno=%d (%s)\n",p,errno,strerror(errno));
    else { printf("precise_ip=%d => OK (fd=%ld)\n",p,fd); close(fd); }
  }
  return 0;
}
