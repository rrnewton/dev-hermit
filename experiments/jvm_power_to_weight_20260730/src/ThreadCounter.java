import java.util.concurrent.atomic.AtomicLong;

// Multithread: contended atomic increments across N threads. Exercises
// clone/futex, JVM thread lifecycle, and the deterministic scheduler.
public class ThreadCounter {
    public static void main(String[] args) throws InterruptedException {
        final int threads = 4;
        final int perThread = 5_000;
        final AtomicLong counter = new AtomicLong(0);
        Thread[] ts = new Thread[threads];
        for (int i = 0; i < threads; i++) {
            ts[i] = new Thread(() -> {
                for (int j = 0; j < perThread; j++) {
                    counter.incrementAndGet();
                }
            });
        }
        for (Thread t : ts) t.start();
        for (Thread t : ts) t.join();
        System.out.println("counter=" + counter.get());
    }
}
