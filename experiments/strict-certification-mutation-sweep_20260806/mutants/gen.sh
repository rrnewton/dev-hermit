set -e
STATE=/tmp/w7mutstate
# counter helper: appends one byte to $STATE, returns new size
COMMON='
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
static long bump(void){
  int fd = open("/tmp/w7mutstate", O_RDWR|O_CREAT|O_APPEND, 0644);
  if (fd < 0) { perror("open state"); exit(99); }
  if (write(fd, "x", 1) != 1) { perror("write state"); exit(98); }
  long n = lseek(fd, 0, SEEK_END);
  close(fd);
  return n;
}
'
# 1. clean control: deterministic, no state
cat > clean_ctrl.c <<'C'
#include <stdio.h>
int main(void){ printf("constant-output\n"); return 0; }
C
# 2. stdout divergence
printf '%s' "$COMMON" > mut_stdout.c
cat >> mut_stdout.c <<'C'
int main(void){ long n = bump(); printf("counter=%ld\n", n); return 0; }
C
# 3. exit-status divergence
printf '%s' "$COMMON" > mut_exit.c
cat >> mut_exit.c <<'C'
int main(void){ long n = bump(); printf("constant-output\n"); return (int)(n % 200); }
C
# 4. DETLOG-only divergence: read N bytes (N grows), stdout+exit constant
printf '%s' "$COMMON" > mut_detlog_only.c
cat >> mut_detlog_only.c <<'C'
int main(void){
  bump();
  int fd = open("/tmp/w7mutstate", O_RDONLY);
  char buf[4096]; ssize_t r = read(fd, buf, sizeof buf); (void)r; close(fd);
  printf("constant-output\n"); return 0;
}
C
# 5. address-only divergence: pointer arg to a zero-length write varies
printf '%s' "$COMMON" > mut_addr.c
cat >> mut_addr.c <<'C'
int main(void){
  long n = bump();
  static char big[1<<20];
  char *p = big + (n % 512) * 64;
  ssize_t r = write(1, p, 0); (void)r;
  printf("constant-output\n"); return 0;
}
C
# 6. path-only divergence: openat path arg varies, content identical
printf '%s' "$COMMON" > mut_path.c
cat >> mut_path.c <<'C'
int main(void){
  long n = bump();
  char path[256]; snprintf(path, sizeof path, "/tmp/w7mutpath_%ld", n);
  int fd = open(path, O_RDWR|O_CREAT, 0644); if (fd>=0) close(fd);
  printf("constant-output\n"); return 0;
}
C
for f in clean_ctrl mut_stdout mut_exit mut_detlog_only mut_addr mut_path; do
  gcc -O0 -static -o $f $f.c
done
ls -la
