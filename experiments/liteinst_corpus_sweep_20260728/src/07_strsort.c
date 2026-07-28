#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int cmp(const void*a,const void*b){return strcmp(*(const char**)a,*(const char**)b);}
int main(void){
  const char*w[]={"pear","apple","fig","cherry","banana","date","elderberry"};
  int n=7; qsort(w,n,sizeof w[0],cmp);
  for(int i=0;i<n;i++) printf("%s\n",w[i]); return 0;
}
