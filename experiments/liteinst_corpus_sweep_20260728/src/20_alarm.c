#include <stdio.h>
#include <unistd.h>
#include <signal.h>
static volatile sig_atomic_t rang=0;
static void on(int s){(void)s;rang=1;}
int main(void){
  signal(SIGALRM,on); alarm(1);
  while(!rang){ /* spin briefly */ static volatile long x; for(long i=0;i<100000;i++) x+=i; if(x<0)break; }
  printf("rang=%d\n",(int)rang); return 0;
}
