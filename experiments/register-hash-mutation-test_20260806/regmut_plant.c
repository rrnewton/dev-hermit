/* PLANT: a real register divergence AT a guest-logical-control point.
 * RDRAND is not determinized by Detcore (masked in CPUID only), so its value
 * differs run to run. Park it in a CALLEE-SAVED register held across a syscall
 * and never print it: stdout is byte-identical, so only the register hash at
 * the control point can see the difference. */
static long sys(long n,long a,long b,long c){long r;
  __asm__ volatile("syscall":"=a"(r):"a"(n),"D"(a),"S"(b),"d"(c):"rcx","r11","memory");return r;}
void _start(void){
  unsigned long v=0; unsigned char ok=0;
  for(int i=0;i<10 && !ok;i++) __asm__ volatile("rdrand %0; setc %1":"=r"(v),"=qm"(ok)::"cc");
  register unsigned long marker __asm__("r13") = v;   /* differs run to run */
  __asm__ volatile("" :: "r"(marker));
  sys(1,1,(long)"identical\n",10);                     /* identical stdout   */
  __asm__ volatile("" :: "r"(marker));
  sys(60,0,0,0);
}
