import java.util.HashMap;
import java.util.TreeMap;

// Data-structure micro: String.hashCode, HashMap/TreeMap churn. Exercises the
// core library object graph and hashing without I/O. Deterministic checksum.
public class HashMapString {
    public static void main(String[] args) {
        HashMap<String, Long> h = new HashMap<>();
        for (int i = 0; i < 20_000; i++) {
            String k = "key-" + (i % 4096) + "-" + Integer.toHexString((int) (i * 2654435761L >>> 16 & 0xffff));
            h.merge(k, (long) i, Long::sum);
        }
        TreeMap<String, Long> sorted = new TreeMap<>(h);
        long sum = 0;
        for (Long v : sorted.values()) sum += v;
        System.out.println("hashmap sum=" + sum + " keys=" + (sorted.size() % 100));
    }
}
