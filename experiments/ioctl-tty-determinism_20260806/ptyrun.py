#!/usr/bin/env python3
"""Run a command with its standard fds on terminals of a chosen size, and
capture the guest's OWN stdout without hermit's diagnostics mixed in.

  ptyrun.py <rows> <cols> -- <cmd> [args...]     # PTY mode
  ptyrun.py pipe         -- <cmd> [args...]      # no terminal at all

Why two PTYs in PTY mode. The measurement channel is the guest's stdout, but
hermit writes its own trace to stderr, and only the ptrace backend honours
`--log-file`: sabre duplicates its log to stderr (38 KB for `ls -C /etc`) and
dbi ignores `--log-file` entirely (68 KB to stderr). A single shared PTY
therefore interleaves hermit's trace into the guest's output stream, sometimes
mid-line, which no line filter can undo.

So: fd 0 and fd 1 get PTY "out", fd 2 gets PTY "err". Both are real terminals
of the same size, so isatty / TIOCGWINSZ / TCGETS behave identically on all
three descriptors; only the capture is separated. The child is a session
leader with PTY "out" as its controlling terminal, so /dev/tty resolves.

Consequence to be aware of when reading probe output: ttyname(2) legitimately
differs from ttyname(0)/ttyname(1) here, because fd 2 really is a different
terminal. That is constant across runs and geometries and so affects no
comparison in this experiment.

Prints the guest's stdout with CR stripped: a PTY in ONLCR mode turns every
\\n into \\r\\n, which is a property of the terminal, not of the guest.
"""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios


def _size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def run_pty(rows, cols, argv):
    out_m, out_s = pty.openpty()
    err_m, err_s = pty.openpty()
    _size(out_m, rows, cols)
    _size(err_m, rows, cols)

    def become_session_leader():
        # setsid() alone is not enough: the slave was opened by the parent, so
        # it is not automatically the new session's controlling terminal.
        # TIOCSCTTY makes it one, which is what /dev/tty and TIOCGSID need.
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        argv, stdin=out_s, stdout=out_s, stderr=err_s,
        preexec_fn=become_session_leader, close_fds=True,
    )
    os.close(out_s)
    os.close(err_s)

    chunks, open_fds = [], {out_m, err_m}
    while open_fds:
        r, _, _ = select.select(list(open_fds), [], [], 120.0)
        if not r:
            proc.kill()
            break
        for fd in r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                open_fds.discard(fd)
                continue
            if fd is out_m or fd == out_m:
                chunks.append(data)
            # err_m is drained and discarded: it exists so the child never
            # blocks on a full stderr buffer, not to be measured.
    rc = proc.wait()
    for fd in (out_m, err_m):
        try:
            os.close(fd)
        except OSError:
            pass
    return b"".join(chunks).replace(b"\r\n", b"\n"), rc


def run_pipe(argv):
    """No terminal on any fd. stderr goes to its own pipe and is discarded, so
    the captured stream is the guest's stdout alone."""
    proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=120)
    return proc.stdout, proc.returncode


def main():
    args = sys.argv[1:]
    sep = args.index("--")
    head, argv = args[:sep], args[sep + 1:]
    if head[0] == "pipe":
        out, rc = run_pipe(argv)
    else:
        out, rc = run_pty(int(head[0]), int(head[1]), argv)
    sys.stdout.buffer.write(out)
    sys.stdout.flush()
    sys.exit(rc)


if __name__ == "__main__":
    main()
