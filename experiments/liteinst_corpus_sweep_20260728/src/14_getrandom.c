#include <stdio.h>
#include <sys/random.h>
int main(void){
  unsigned char buf[16]; ssize_t r=getrandom(buf,sizeof buf,0);
  if(r!=sizeof buf){ perror("getrandom"); return 1; }
  unsigned long h=1469598103934665603UL;
  for(size_t i=0;i<sizeof buf;i++){ h^=buf[i]; h*=1099511628211UL; }
  printf("rnd_fnv=%016lx\n",h); return 0;
}
