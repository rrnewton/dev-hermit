#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void){
  enum { N=64, SZ=8192 };
  char *keep[N];
  for (int i=0;i<N;i++){ keep[i]=malloc(SZ); memset(keep[i], 'a'+(i%26), SZ); }
  for (int i=0;i<N;i+=2){ free(keep[i]); keep[i]=NULL; }          // fragment
  for (int i=0;i<N;i+=2){ keep[i]=malloc(SZ*2); memset(keep[i],'Z',SZ*2); }
  unsigned long s=0; for(int i=0;i<N;i++) if(keep[i]) s+=(unsigned char)keep[i][0];
  printf("heap-sum=%lu\n", s);
  for (int i=0;i<N;i++) free(keep[i]);
  return 0;
}
