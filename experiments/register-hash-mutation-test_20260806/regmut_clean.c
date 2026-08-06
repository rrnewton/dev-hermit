/* CONTROL: identical shape, but the register holds a CONSTANT. */
static long sys(long n,long a,long b,long c){long r;
  __asm__ volatile("syscall":"=a"(r):"a"(n),"D"(a),"S"(b),"d"(c):"rcx","r11","memory");return r;}
void _start(void){
  register unsigned long marker __asm__("r13") = 0xC0FFEE;
  __asm__ volatile("" :: "r"(marker));
  sys(1,1,(long)"identical\n",10);
  __asm__ volatile("" :: "r"(marker));
  sys(60,0,0,0);
}
