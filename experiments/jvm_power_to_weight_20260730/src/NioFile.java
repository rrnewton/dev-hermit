import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

// NIO file micro: create a temp file, write via a mapped/positioned channel,
// read it back. Exercises openat/pwrite/pread/mmap/close + the NIO stack.
public class NioFile {
    public static void main(String[] args) throws Exception {
        Path p = Files.createTempFile("hermit-nio", ".bin");
        try (FileChannel ch = FileChannel.open(p,
                StandardOpenOption.READ, StandardOpenOption.WRITE)) {
            ByteBuffer buf = ByteBuffer.allocate(4096);
            long checksum = 0;
            for (int block = 0; block < 64; block++) {
                buf.clear();
                for (int i = 0; i < buf.capacity(); i++) buf.put((byte) ((block + i) & 0xff));
                buf.flip();
                ch.write(buf, (long) block * buf.capacity());
            }
            ch.force(true);
            ByteBuffer rd = ByteBuffer.allocate(4096);
            for (int block = 0; block < 64; block++) {
                rd.clear();
                ch.read(rd, (long) block * rd.capacity());
                rd.flip();
                while (rd.hasRemaining()) checksum += rd.get() & 0xff;
            }
            System.out.println("nio file checksum=" + checksum);
        } finally {
            Files.deleteIfExists(p);
        }
    }
}
