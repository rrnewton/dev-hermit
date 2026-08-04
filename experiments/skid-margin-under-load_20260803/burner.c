/* Branch-heavy CPU burner: creates scheduling + interrupt-delivery pressure
 * that mimics a saturated CI box. Runs until killed. One process = ~1 core. */
int main(void) {
  volatile unsigned x = 0;
  const unsigned one = 1;
  for (;;) {
    /* branch-dense inner loop so burners retire conditional branches too,
     * matching the RCB-contention profile of real guest workloads. */
    for (int i = 0; i < 1000000; i++) {
      __asm__ volatile("test %[one], %[one]\n\t"
                       "jnz 1f\n\t"
                       "1:"
                       :
                       : [one] "r"(one)
                       : "cc");
      x += i;
    }
  }
  return (int)x;
}
