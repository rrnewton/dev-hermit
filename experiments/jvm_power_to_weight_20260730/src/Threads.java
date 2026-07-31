import java.util.Arrays;

public class Threads {
    public static void main(String[] args) throws InterruptedException {
        final int n = 8;
        final int[] results = new int[n];
        Thread[] ts = new Thread[n];
        for (int i = 0; i < n; i++) {
            final int idx = i;
            ts[i] = new Thread(() -> results[idx] = idx * idx);
            ts[i].start();
        }
        for (Thread t : ts) {
            t.join();
        }
        int sum = 0;
        for (int v : results) {
            sum += v;
        }
        System.out.println("threads sum=" + sum + " results=" + Arrays.toString(results));
    }
}
