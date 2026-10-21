"""Fan watchdog, run as its own systemd unit so it outlives the plugin.

While the plugin holds the fan in manual mode it keeps a state file and a
heartbeat in RUN_DIR. If the heartbeat goes stale (plugin crashed, hung or
was killed) this puts the fan back under firmware control and exits. If the
state file disappears, the plugin returned the fan to auto itself, so the
watchdog just exits.

Standalone on purpose: no imports from the plugin package.
"""

import json
import os
import sys
import time

RUN_DIR = "/run/hhctl"
STATE = f"{RUN_DIR}/fan-watchdog.json"
HEARTBEAT = f"{RUN_DIR}/heartbeat"
PIDFILE = f"{RUN_DIR}/watchdog.pid"
EC_IO = "/sys/kernel/debug/ec/ec0/io"
STALE = 20.0  # seconds of CLOCK_MONOTONIC, which does not advance in suspend
POLL = 3.0


def restore(spec: dict) -> None:
    if spec["kind"] == "hwmon":
        with open(f"{spec['path']}/pwm1_enable", "w") as f:
            f.write("2\n")
    elif spec["kind"] == "ec":
        with open(EC_IO, "r+b", buffering=0) as f:
            f.seek(spec["mode_reg"])
            # Only flip the register if it still holds our manual value.
            if f.read(1)[0] == spec["manual"]:
                f.seek(spec["mode_reg"])
                f.write(bytes([spec["auto"]]))


def heartbeat_age() -> float:
    try:
        with open(HEARTBEAT) as f:
            return time.monotonic() - float(f.read())
    except (OSError, ValueError):
        return float("inf")


def main() -> int:
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    try:
        while True:
            try:
                with open(STATE) as f:
                    spec = json.load(f)
            except FileNotFoundError:
                return 0
            except (OSError, ValueError):
                spec = None
            if spec and heartbeat_age() > STALE:
                print("hhctl: plugin heartbeat stale, restoring fan auto", file=sys.stderr)
                try:
                    restore(spec)
                except OSError as e:
                    print(f"hhctl: restore failed, retrying: {e}", file=sys.stderr)
                    time.sleep(POLL)
                    continue
                os.unlink(STATE)
                return 0
            time.sleep(POLL)
    finally:
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
