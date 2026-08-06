/* One binary, many termination modes selected by argv[1]. Prints an ordering
 * trace on stdout where relevant; the WAIT STATUS is the primary observable. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <pthread.h>
static void ax1(void){ printf("atexit1\n"); }
static void ax2(void){ printf("atexit2\n"); }
static void *thr_exit(void *a){ (void)a; printf("thread-exiting-group\n"); fflush(stdout); exit(7); }
static void *thr_noop(void *a){ (void)a; return NULL; }
static long recurse(long n){ volatile char pad[4096]; pad[0]=(char)n; return n<=0?0:recurse(n+1)+pad[0]; }
int main(int argc, char **argv){
    const char *m = argc>1?argv[1]:"exit0";
    if(!strcmp(m,"exit0")) return 0;
    if(!strcmp(m,"exit42")) return 42;
    if(!strcmp(m,"exit256")) exit(256);            /* truncates to 0 */
    if(!strcmp(m,"exitneg")) exit(-1);             /* truncates to 255 */
    if(!strcmp(m,"_exit9")) _exit(9);
    if(!strcmp(m,"atexit")){ atexit(ax1); atexit(ax2); return 3; }
    if(!strcmp(m,"abort")) abort();
    if(!strcmp(m,"segv")){ volatile int *p=0; *p=1; }
    if(!strcmp(m,"fpe")){ volatile int z=0, q=1/z; (void)q; }
    if(!strcmp(m,"ill")) __builtin_trap();
    if(!strcmp(m,"raise_term")) raise(SIGTERM);
    if(!strcmp(m,"raise_kill")) raise(SIGKILL);
    if(!strcmp(m,"raise_trap")) raise(SIGTRAP);
    if(!strcmp(m,"stackoverflow")) return (int)recurse(1);
    if(!strcmp(m,"thread_exit")){ pthread_t t; pthread_create(&t,NULL,thr_exit,NULL); for(;;) pause(); }
    if(!strcmp(m,"thread_join_exit")){ pthread_t t; pthread_create(&t,NULL,thr_noop,NULL); pthread_join(t,NULL); return 5; }
    fprintf(stderr,"unknown mode %s\n",m); return 100;
}
