#include <stdio.h>
#include <stdlib.h>
int main(void){ const char*p=getenv("PATH"); printf("has_path=%d\n", p?1:0); return 0; }
