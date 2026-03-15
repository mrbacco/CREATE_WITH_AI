import argparse
import re
import subprocess
import sys

from app import app
from config import APP_DEBUG


def _pids_on_port(port):
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []

    pids = set()
    pattern = re.compile(rf"^\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$")
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match:
            pids.add(int(match.group(1)))
    return sorted(pids)


def _kill_pid(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)


def run_app(force=False, port=5050):
    if force:
        for pid in _pids_on_port(port):
            _kill_pid(pid)

    app.run(debug=APP_DEBUG, port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Flask app with optional force port takeover.")
    parser.add_argument("--force", action="store_true", help="Kill existing listeners on port 5050 before start.")
    parser.add_argument("--port", type=int, default=5050, help="Port to bind (default: 5050).")
    args = parser.parse_args()

    try:
        run_app(force=args.force, port=args.port)
    except OSError as exc:
        if getattr(exc, "errno", None) in {48, 98, 10013, 10048}:
            print("ERROR: Port is already in use. Re-run with --force to stop existing listener.")
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)