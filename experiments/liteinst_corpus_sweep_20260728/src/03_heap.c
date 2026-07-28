#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void){
  size_t n=4096; unsigned char *b=malloc(n);
  for(size_t i=0;i<n;i++) b[i]=(unsigned char)(i*31+7);
  unsigned long h=1469598103934665603UL;
  for(size_t i=0;i<n;i++){ h^=b[i]; h*=1099511628211UL; }
  printf("fnv=%016lx\n",h); free(b); return 0;
}
