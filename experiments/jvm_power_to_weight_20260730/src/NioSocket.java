import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;

// NIO/socket micro: loopback TCP echo in a single process. Exercises
// socket/bind/listen/accept/connect/read/write + a second JVM thread.
public class NioSocket {
    public static void main(String[] args) throws Exception {
        InetAddress lo = InetAddress.getByName("127.0.0.1");
        try (ServerSocket server = new ServerSocket(0, 1, lo)) {
            int port = server.getLocalPort();
            Thread srv = new Thread(() -> {
                try (Socket s = server.accept();
                     InputStream in = s.getInputStream();
                     OutputStream out = s.getOutputStream()) {
                    byte[] buf = new byte[512];
                    int n;
                    while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                    out.flush();
                } catch (Exception e) { /* server closes on client EOF */ }
            });
            srv.start();
            long checksum = 0;
            try (Socket c = new Socket(lo, port);
                 OutputStream out = c.getOutputStream();
                 InputStream in = c.getInputStream()) {
                for (int round = 0; round < 32; round++) {
                    byte[] msg = ("ping-" + round + "\n").getBytes("UTF-8");
                    out.write(msg);
                    out.flush();
                    byte[] echo = new byte[msg.length];
                    int off = 0;
                    while (off < echo.length) {
                        int r = in.read(echo, off, echo.length - off);
                        if (r < 0) break;
                        off += r;
                    }
                    for (byte b : echo) checksum += b & 0xff;
                }
            }
            srv.join(5000);
            System.out.println("nio socket checksum=" + checksum);
        }
    }
}
