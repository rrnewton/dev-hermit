#include <stdio.h>
#include <sys/mman.h>
#include <string.h>
int main(void){
  size_t n=1<<16; unsigned char*p=mmap(NULL,n,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
  if(p==MAP_FAILED){ perror("mmap"); return 1; }
  for(size_t i=0;i<n;i++) p[i]=(unsigned char)(i^0xA5);
  unsigned long s=0; for(size_t i=0;i<n;i++) s+=p[i];
  printf("mmap_sum=%lu\n",s); munmap(p,n); return 0;
}
