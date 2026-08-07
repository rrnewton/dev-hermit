/* REFERENCE GUEST for the RDRAND / RDSEED axis.
 *
 * THIS AXIS IS MARKED NOT-COMPARABLE. The guest is checked in anyway, because
 * the marking has to be re-derivable: this is the artifact that establishes it.
 *
 * It executes cpuid / rdtsc / rdrand / rdseed DIRECTLY, ignoring what CPUID
 * advertises, which is the whole point. Hermit masks the RDRAND CPUID bit
 * (detcore/src/cpuid.rs:39-40, "masked off to prevent non-determinism") and the
 * mask IS applied -- CPUID.1:ECX bit30 goes SET -> CLEARED. But the mask is
 * ADVISORY: the instruction still executes, still returns CF=1, and still
 * yields host entropy.
 *
 * Measured under ptrace, n=6, same binary and same runs:
 *     cpuid  1 distinct / 6   STABLE      (kernel fault control: CPUID faulting)
 *     rdtsc  1 distinct / 6   STABLE      (kernel fault control: CR4.TSD)
 *     rdrand 6 distinct / 6   ALL DIFFER  (no kernel fault control exists)
 *     rdseed 6 distinct / 6   ALL DIFFER  (no kernel fault control exists)
 * The two stable rows are the built-in positive control: this guest can report
 * "stable", so the two unstable rows are not a measurement artifact.
 */
#include <stdio.h>
#include <stdint.h>
int main(void){
  unsigned a,b,c,d;
  __asm__ volatile("cpuid":"=a"(a),"=b"(b),"=c"(c),"=d"(d):"a"(1),"c"(0));
  printf("cpuid1_ecx=%08x cpuid1_edx=%08x\n", c, d);
  unsigned lo,hi; __asm__ volatile("rdtsc":"=a"(lo),"=d"(hi));
  printf("rdtsc=%08x%08x\n", hi, lo);
  unsigned long r=0; unsigned char ok=0;
  __asm__ volatile("rdrand %0; setc %1":"=r"(r),"=qm"(ok)::"cc");
  printf("rdrand ok=%u val=%016lx\n", ok, ok?r:0UL);
  unsigned long s=0; unsigned char ok2=0;
  __asm__ volatile("rdseed %0; setc %1":"=r"(s),"=qm"(ok2)::"cc");
  printf("rdseed ok=%u val=%016lx\n", ok2, ok2?s:0UL);
  return 0;
}
