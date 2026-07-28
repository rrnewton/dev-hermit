#include <stdio.h>
#include <pthread.h>
static long acc=0; static pthread_mutex_t m=PTHREAD_MUTEX_INITIALIZER;
static void*worker(void*a){ long n=(long)a; for(int i=0;i<10000;i++){pthread_mutex_lock(&m);acc+=n;pthread_mutex_unlock(&m);} return NULL; }
int main(void){
  pthread_t t[4];
  for(long i=0;i<4;i++) pthread_create(&t[i],NULL,worker,(void*)(i+1));
  for(int i=0;i<4;i++) pthread_join(t[i],NULL);
  printf("acc=%ld\n",acc); return 0;
}
