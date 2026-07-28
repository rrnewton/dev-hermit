#include <stdio.h>
#include <math.h>
int main(void){
  double s=0; for(int i=1;i<=1000;i++) s+=sqrt((double)i)+sin((double)i*0.001);
  printf("s=%.6f\n",s); return 0;
}
