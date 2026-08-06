#include <stdint.h>
#include <stdio.h>
static int rd(uint64_t*o){unsigned char c;__asm__ volatile("rdrand %0; setc %1":"=r"(*o),"=qm"(c)::"cc");return c;}
int main(void){uint64_t v=0;for(int i=0;i<10;i++) if(rd(&v)) break;
  /* consume it internally, print nothing derived from it */
  volatile uint64_t sink = v; (void)sink;
  printf("done\n"); return 0; }
