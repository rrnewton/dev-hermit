#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
int main(void){
  int fl = fcntl(0, F_GETFL);
  char b[64]; ssize_t n; int total=0, eagain=0, err=0;
  while(1){ n = read(0,b,sizeof b);
    if(n>0){ total+=n; continue; }
    if(n==0) break;
    if(errno==EAGAIN){ eagain++; if(eagain>3) break; continue; }
    err=errno; break; }
  fprintf(stderr,"PROBE fl=0x%x O_NONBLOCK=%d bytes=%d eagain=%d err=%s\n",
     fl, !!(fl & O_NONBLOCK), total, eagain, err?strerror(err):"none");
  return 0; }
