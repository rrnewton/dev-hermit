#include <stdio.h>
#include <stdlib.h>
int main(void){
  char path[]="/tmp/liteinst_corpus_XXXXXX";
  int fd=mkstemp(path); if(fd<0){perror("mkstemp");return 1;}
  FILE*f=fdopen(fd,"w+");
  for(int i=0;i<100;i++) fprintf(f,"line %d\n",i);
  fflush(f); rewind(f);
  long total=0; char buf[64];
  while(fgets(buf,sizeof buf,f)) total+=buf[0];
  printf("total=%ld\n",total);
  fclose(f); remove(path); return 0;
}
