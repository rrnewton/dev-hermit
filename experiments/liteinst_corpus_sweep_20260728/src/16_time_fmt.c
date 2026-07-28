#include <stdio.h>
#include <time.h>
int main(void){
  time_t t = time(NULL);
  struct tm *tm = gmtime(&t);
  /* Under hermit strict, time() is determinized; print year bucket to observe it */
  printf("year_ge_1970=%d\n", (tm->tm_year + 1900) >= 1970 ? 1 : 0);
  return 0;
}
