#include <stdio.h>
#include <signal.h>
#include <unistd.h>
static volatile sig_atomic_t hit=0;
static void h(int s){ (void)s; hit=1; }
int main(void){
  signal(SIGUSR1, h);
  raise(SIGUSR1);
  printf("hit=%d\n",(int)hit); return 0;
}
