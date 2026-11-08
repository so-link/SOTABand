"""Container-side timeout survives loss of the host scheduler."""

import os
import signal
import subprocess
import sys


def main():
    timeout = int(sys.argv[1])
    child = subprocess.Popen(sys.argv[2:], start_new_session=True)

    def stop(signum, frame):
        os.killpg(child.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        print("Academic job exceeded its execution timeout", file=sys.stderr, flush=True)
        return 124


if __name__ == "__main__":
    sys.exit(main())
