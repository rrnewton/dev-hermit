// JIT hot-loop: a hot method invoked far past the compile threshold so the
// tiered compiler promotes it C1 -> C2. Pure compute, deterministic result.
public class JitHotLoop {
    // Collatz-style mixing keeps the method non-trivial so C2 has real work.
    static long step(long n) {
        return (n & 1L) == 0L ? n >>> 1 : 3L * n + 1L;
    }

    public static void main(String[] args) {
        long total = 0;
        for (long seed = 1; seed <= 200_000L; seed++) {
            long n = seed;
            int iters = 0;
            while (n != 1 && iters < 10_000) {
                n = step(n);
                iters++;
            }
            total += iters;
        }
        System.out.println("jit collatz total=" + total);
    }
}
