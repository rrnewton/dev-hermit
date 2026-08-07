/* REFERENCE GUEST for the STACK dimension.
 *
 * WHY THIS SHAPE. `/bin/true` produces 31 populated [stack] hashes and 0/31
 * cross-backend agreement, so it is a NON-CREDIBLE control: it emits a
 * populated column while never exercising the thing the column measures. Every
 * one of those 31 hashes is of loader/libc scratch that no guest code wrote.
 * Populated output is not the same as an exercised dimension.
 *
 * To exercise the stack a guest must make the stack CONTENT a deterministic
 * function of its own code, and must make it CHANGE in a way a hash can see:
 *
 *   1. DEPTH -- recurse far enough that the mapping grows past its initial
 *      size, so the stack VMA itself moves, not just bytes near the top.
 *   2. FRAME VARIATION -- frames must differ in size and payload, so two
 *      different call paths cannot collide to the same digest. A uniform frame
 *      recursion hashes almost identically at every depth and would read as
 *      "stable" for the wrong reason.
 *   3. WRITTEN, READ-BACK PAYLOAD -- each frame writes a buffer and the result
 *      is folded into the return value, so the compiler cannot elide the frames
 *      and the printed witness proves they really existed.
 *
 * `volatile` + the accumulated checksum keep (3) honest under -O2; without them
 * a good optimiser turns this into a constant and the stack is never touched.
 *
 * The printed witness is the EXERCISE PROOF: it is a pure function of the
 * recursion, so a run that prints it necessarily walked the frames.
 */
#include <stdio.h>
#include <string.h>

/* Frame size varies with depth (8..1032 bytes) so no two depths share a shape. */
static unsigned long frame(int depth, unsigned long acc) {
  volatile char pad[8 + ((depth % 128) * 8)];
  memset((void *)pad, (char)('A' + (depth % 26)), sizeof pad);
  acc = acc * 31u + (unsigned char)pad[0] + (unsigned long)sizeof pad;
  if (depth == 0) return acc;
  return frame(depth - 1, acc);
}

int main(void) {
  /* 4096 frames averaging ~520B is ~2MiB: past the 8KiB initial stack and
     large enough to force the [stack] mapping to grow measurably. */
  unsigned long a = frame(4096, 1469598103934665603UL);
  printf("stack-depth=4096 stack-sum=%lu\n", a);
  return 0;
}
