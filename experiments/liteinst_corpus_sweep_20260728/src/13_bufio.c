#include <stdio.h>
int main(void){
  for(int i=0;i<50;i++){ fputs("chunk\n",stdout); }
  fprintf(stderr,"done-stderr\n");
  return 0;
}
