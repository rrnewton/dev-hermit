/* REFERENCE GUEST for the DETLOG dimension.
 *
 * The detlog dimension compares Detcore's INFO-level record stream. A guest
 * qualifies only if it drives a nontrivial, deterministic SEQUENCE of
 * determinized operations -- time, scheduling, and file descriptors -- so the
 * comparison has records of substance to align.
 *
 * `/bin/true` produces only loader traffic and an exit: the record count is
 * dominated by process setup that no guest code caused, so a match says
 * nothing about the backend's handling of guest behaviour.
 *
 * Every operation here is determinized by Detcore (virtual clock, virtual pid,
 * deterministic fd allocation), so the record stream is reproducible.
 */
#include <stdio.h>
#include <time.h>
#include <unistd.h>

/* The witness counts OPERATIONS THAT SUCCEEDED, never the values they returned.
 *
 * An earlier draft folded `ts.tv_nsec & 1` into the witness. That was wrong in
 * two ways, and measuring it caught both: it varied run to run even natively
 * (16/18/19/18/13 over five runs), and -- worse -- a witness derived from a
 * determinized VALUE silently doubles as a parity oracle, so "did this guest
 * exercise the dimension" and "do the backends agree" would collapse into one
 * number. Those must stay separate: the witness proves the guest ran its own
 * code; agreement is what the harness measures afterwards. */
int main(void) {
  struct timespec ts;
  unsigned long ops = 0;
  for (int i = 0; i < 32; i++) {
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) ops++;  /* virtual time */
    if (getpid() > 0) ops++;                               /* virtual pid */
    int fd = dup(1);                                       /* deterministic fd */
    if (fd >= 0) { ops++; if (close(fd) == 0) ops++; }
  }
  printf("detlog-iters=32 detlog-ops=%lu\n", ops);
  return 0;
}
