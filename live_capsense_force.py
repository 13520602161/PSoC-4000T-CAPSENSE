#!/usr/bin/env python3
"""Live CAPSENSE force plot over serial; --demo replays a Tuner CSV for testing.

Wire protocol: ASCII lines "diff_count,status" (for example "2480,1").
Force model: 0 counts -> 0 N; 5703 counts -> 100 N (assumption only).
"""

import argparse
import csv
from collections import deque
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import time

import matplotlib.animation as animation
import matplotlib.pyplot as plt


PEAK_COUNT = 5703.0
PEAK_FORCE_N = 100.0


def parse_sample(line):
    fields = line.strip().split(",")
    if len(fields) not in (1, 2):
        raise ValueError("expected diff or diff,status")
    diff = int(fields[0])
    status = int(fields[1]) if len(fields) == 2 else ""
    if diff < 0 or (status != "" and status not in (0, 1)):
        raise ValueError("invalid count/status")
    return diff, status


def serial_reader(port, baud, queue, stop):
    try:
        import serial
        with serial.Serial(port, baudrate=baud, timeout=0.2) as device:
            # Board resets on some USB adapters; timeouts keep closure responsive.
            while not stop.is_set():
                packet = device.readline().decode("ascii", errors="ignore")
                if not packet:
                    continue
                try:
                    diff, status = parse_sample(packet)
                except ValueError:
                    continue
                queue.put((time.monotonic(), diff, status))
    except Exception as exc:
        queue.put((None, "Connection error: " + str(exc), ""))


def demo_reader(path, queue, stop):
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = csv.DictReader(source)
            start = time.monotonic()
            first_ms = None
            for row in rows:
                if stop.is_set():
                    break
                source_ms = float(row["Relative time"])
                if first_ms is None:
                    first_ms = source_ms
                deadline = start + (source_ms - first_ms) / 1000
                if stop.wait(max(0, deadline - time.monotonic())):
                    break
                queue.put((time.monotonic(), int(row["Button0_Sns0 DiffCount"]),
                           int(row["Button0_Sns0 Status"])))
    except Exception as exc:
        queue.put((None, "Demo error: " + str(exc), ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--port", help="KitProg3 serial port, e.g. COM5")
    choice.add_argument("--demo", type=Path, help="replay an existing Tuner CSV in real time")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--peak-count", type=float, default=PEAK_COUNT)
    parser.add_argument("--peak-force", type=float, default=PEAK_FORCE_N)
    parser.add_argument("--window", type=float, default=20, help="visible seconds")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if min(args.peak_count, args.peak_force, args.window) <= 0:
        parser.error("peak-count, peak-force and window must be positive")
    if args.demo and not args.demo.is_file():
        parser.error(f"demo file not found: {args.demo}")
    if args.port:
        try:
            import serial  # noqa: F401
        except ImportError:
            parser.error("serial mode needs pyserial: python -m pip install pyserial")

    output = args.output or Path("live_force_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    queue, stop = Queue(), Event()
    worker = Thread(target=demo_reader if args.demo else serial_reader,
                    args=(args.demo, queue, stop) if args.demo else (args.port, args.baud, queue, stop),
                    daemon=True)
    worker.start()
    times, values = deque(), deque()
    start_time = None
    fig, ax = plt.subplots(figsize=(11, 5.5))
    (line,) = ax.plot([], [], color="#d95e32", lw=1.7)
    ax.set(xlabel="Elapsed time (s)", ylabel="Estimated force (N)",
           title="Live capacitive pressure response — assumed linear calibration")
    ax.set_ylim(0, args.peak_force * 1.12)
    ax.grid(alpha=0.25)
    current = ax.text(0.02, 0.94, "Waiting for samples...", transform=ax.transAxes,
                      va="top", fontsize=13)
    ax.text(0.02, 0.02, f"Assumption: {args.peak_count:g} counts = {args.peak_force:g} N",
            transform=ax.transAxes, fontsize=9)
    with output.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(["elapsed_s", "diff_count", "touch_status", "estimated_force_N"])

        def update(_):
            nonlocal start_time
            changed = False
            for _ in range(1000):  # prevent plot starvation if the input is too fast
                try:
                    arrival, diff, status = queue.get_nowait()
                except Empty:
                    break
                if arrival is None:
                    current.set_text(diff)
                    stop.set()
                    break
                if start_time is None:
                    start_time = arrival
                seconds = arrival - start_time
                force = min(args.peak_force, args.peak_force * diff / args.peak_count)
                writer.writerow([f"{seconds:.4f}", diff, status, f"{force:.4f}"])
                times.append(seconds)
                values.append(force)
                current.set_text(f"Current: {force:.2f} N    DiffCount: {diff}"
                                 + ("    OVER RANGE" if diff > args.peak_count else ""))
                changed = True
            if changed:
                target.flush()
                while times and times[0] < times[-1] - args.window:
                    times.popleft()
                    values.popleft()
                line.set_data(list(times), list(values))
                ax.set_xlim(max(0, times[-1] - args.window), max(args.window, times[-1]))
            return line, current

        # Keep a reference so Matplotlib does not garbage-collect the animation.
        live = animation.FuncAnimation(fig, update, interval=60, cache_frame_data=False)
        try:
            plt.show()
        finally:
            stop.set()
            worker.join(timeout=1)
            plt.close(fig)
    print(f"Recorded force data: {output}")


if __name__ == "__main__":
    main()
