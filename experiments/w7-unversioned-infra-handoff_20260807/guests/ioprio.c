/* PLANT both claims:
   (1) set-then-get must round-trip;
   (2) a nonexistent target must give ESRCH. */
#include <stdio.h>
#include <errno.h>
#include <unistd.h>
#include <sys/syscall.h>
#define WHO_PROCESS 1
#define CLASS_SHIFT 13
#define PRIO(cls,dat) (((cls)<<CLASS_SHIFT)|(dat))
static long ioprio_set(int w,int who,int p){return syscall(SYS_ioprio_set,w,who,p);}
static long ioprio_get(int w,int who){return syscall(SYS_ioprio_get,w,who);}
int main(void){
    int want = PRIO(2,6);                        /* BE class, data 6 -- no privilege needed */
    long s = ioprio_set(WHO_PROCESS,0,want);
    long g = ioprio_get(WHO_PROCESS,0);
    printf("roundtrip: set(%d)->%ld  get->%ld  match=%d\n", want, s, g, (g==want));
    errno=0;
    long bad = ioprio_get(WHO_PROCESS, 0x7ffffff);   /* nonexistent pid */
    printf("bogus_target: get->%ld errno=%d(%s) is_ESRCH=%d\n",
           bad, errno, errno==ESRCH?"ESRCH":"not-ESRCH", errno==ESRCH);
    return 0;
}
