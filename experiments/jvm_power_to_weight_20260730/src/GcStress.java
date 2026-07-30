// GC-alloc micro: churn short-lived allocations to drive minor GCs
// (mmap/munmap of the young gen, GC worker threads). Deterministic checksum.
public class GcStress {
    public static void main(String[] args) {
        long checksum = 0;
        for (int i = 0; i < 50_000; i++) {
            byte[] b = new byte[256];
            b[i % b.length] = (byte) i;
            checksum += b[i % b.length] & 0xff;
            if ((i & 0x3ff) == 0) {
                int[] survivor = new int[64];
                survivor[0] = i;
                checksum += survivor[0];
            }
        }
        System.out.println("gc checksum=" + checksum);
    }
}
