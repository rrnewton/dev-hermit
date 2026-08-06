/* Drives lots of TOOL-HANDLER activity that is NOT a syscall: CPUID and RDTSC
 * both trap into the Detcore handler, which runs with the guest's registers as
 * scratch. If sampling leaked into handler interiors, these would emit register
 * samples. They must not: the guest does not logically have control there. */
static long sys(long n, long a, long b, long c){long r;
  __asm__ volatile("syscall":"=a"(r):"a"(n),"D"(a),"S"(b),"d"(c):"rcx","r11","memory");return r;}
void _start(void){
  unsigned a,b,c,d; unsigned long lo,hi;
  for (int i=0;i<200;i++){
    __asm__ volatile("cpuid":"=a"(a),"=b"(b),"=c"(c),"=d"(d):"a"(0):);
    __asm__ volatile("rdtsc":"=a"(lo),"=d"(hi));
  }
  sys(1,1,(long)"done\n",5);
  sys(60,0,0,0);
}
