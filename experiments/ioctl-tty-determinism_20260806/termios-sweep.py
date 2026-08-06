#!/usr/bin/env python3
"""Two axes the geometry sweep does not cover.

A. SINGLE-TERMINAL ttyname. The main sweep puts fd 2 on a second pty so the
   guest's stdout can be captured cleanly. That is not the ordinary situation,
   where all three descriptors share one terminal. This re-runs the probe with
   ONE pty for all three fds (accepting that hermit's stderr is mixed in) to
   see what ttyname(3) reports there.

B. HOST TERMIOS. Terminal geometry is not the only host input the guest can
   read: the line discipline itself (echo, canonical mode, the control
   characters, the speeds) is host state. This drives the SAME geometry with
   DIFFERENT host termios settings and asks whether the guest sees them.
"""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios

HERMIT = os.environ.get(
    "HERMIT_BIN",
    "/home/newton/work/dev-hermit/worktrees/dbi/hermit/target/release/hermit",
)
HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ)
ENV.setdefault("LD_LIBRARY_PATH", os.path.expanduser("~/.local/hermit-deps/lu/usr/lib64"))
ENV["TERM"] = "xterm-256color"


def run_single_pty(argv, rows=24, cols=80, tweak=None):
    """One pty shared by fd 0/1/2, like a real terminal session."""
    m, s = pty.openpty()
    fcntl.ioctl(m, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    if tweak:
        attrs = termios.tcgetattr(s)
        tweak(attrs)
        termios.tcsetattr(s, termios.TCSANOW, attrs)

    def leader():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    p = subprocess.Popen(argv, stdin=s, stdout=s, stderr=s,
                         preexec_fn=leader, close_fds=True, env=ENV)
    os.close(s)
    buf = []
    while True:
        r, _, _ = select.select([m], [], [], 90.0)
        if not r:
            p.kill(); break
        try:
            d = os.read(m, 65536)
        except OSError:
            break
        if not d:
            break
        buf.append(d)
    p.wait()
    os.close(m)
    return b"".join(buf).replace(b"\r\n", b"\n").decode("utf8", "replace")


def probe_lines(text, keys):
    return [l for l in text.splitlines() if l.startswith(keys)]


# ---- termios variants. Only the host line discipline differs. --------------
def t_default(a):
    pass


def t_raw(a):
    # no echo, no canonical mode: what a full-screen program leaves behind
    a[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)


def t_slow(a):
    # different line speed and different VMIN/VTIME control characters
    a[4] = termios.B9600
    a[5] = termios.B9600
    a[6][termios.VMIN] = 3
    a[6][termios.VTIME] = 7


VARIANTS = [("default", t_default), ("raw-noecho", t_raw), ("b9600-vmin3", t_slow)]


def main():
    probe = os.path.join(HERE, "probe")
    print("=== A. ttyname with ONE pty shared by fd 0/1/2 ===")
    for label, argv in (
        ("native", [probe]),
        ("ptrace", [HERMIT, "run", "--backend=ptrace", "--strict", "--", probe]),
        ("dbi",    [HERMIT, "run", "--backend=dbi", "--strict", "--", probe]),
        ("sabre",  [HERMIT, "run", "--backend=sabre", "--strict", "--", probe]),
    ):
        out = run_single_pty(argv)
        got = probe_lines(out, ("ttyname.stdout", "devtty="))
        print(f"  {label:7s} {got}")

    print("\n=== B. host termios variation, geometry fixed at 24x80 ===")
    print("    (lines below are exactly what the guest read back)")
    for backend in ("ptrace", "dbi", "sabre"):
        print(f"  -- backend={backend}")
        seen = {}
        for label, tweak in VARIANTS:
            argv = [HERMIT, "run", f"--backend={backend}", "--strict", "--", probe]
            out = run_single_pty(argv, tweak=tweak)
            got = probe_lines(out, ("TCGETS.stdout=", "TCGETS.stdout.cc"))
            seen[label] = got
            for g in got:
                print(f"     {label:12s} {g}")
        distinct = len({tuple(v) for v in seen.values()})
        print(f"     => {distinct} distinct guest-visible termios across "
              f"{len(VARIANTS)} host settings: "
              f"{'HOST TERMIOS LEAKS' if distinct > 1 else 'virtualized (stable)'}")

    print("\n=== B-control: does the HOST itself vary across these settings? ===")
    for label, tweak in VARIANTS:
        out = run_single_pty([probe], tweak=tweak)
        got = probe_lines(out, ("TCGETS.stdout=",))
        print(f"  native {label:12s} {got}")


if __name__ == "__main__":
    main()
