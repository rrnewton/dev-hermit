static long sc(long n,long a,long b,long c,long d,long e,long f){long r;
 register long r10 __asm__("r10")=d; register long r8 __asm__("r8")=e; register long r9 __asm__("r9")=f;
 __asm__ volatile("syscall":"=a"(r):"a"(n),"D"(a),"S"(b),"d"(c),"r"(r10),"r"(r8),"r"(r9):"rcx","r11","memory");return r;}
__attribute__((noreturn)) static void die(int s){sc(231,s,0,0,0,0,0);__builtin_unreachable();}
void _start(void){
  long base = sc(12,0,0,0,0,0,0);              /* brk(0) -> current break */
  for (int i=1;i<=8;i++){
    char *p = (char*)sc(12, base + i*4096, 0,0,0,0,0);   /* grow the heap */
    if ((long)p > base) { volatile char *q=(volatile char*)(base + (i-1)*4096); *q = (char)i; }
  }
  die(0);
}
