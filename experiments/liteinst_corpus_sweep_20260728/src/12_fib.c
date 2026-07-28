#include <stdio.h>
static long fib(int n){ return n<2?n:fib(n-1)+fib(n-2); }
int main(void){ printf("fib30=%ld\n",fib(30)); return 0; }
