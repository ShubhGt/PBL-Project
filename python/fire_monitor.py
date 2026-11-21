import argparse
import csv
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

SMOKE_HIGH = 400
FLAME_HIGH = 300
TEMP_HIGH = 40.0        # warning above this (a normal room is about 37 C or less)
TEMP_CRITICAL = 45.0    # critical at or above this (DHT11 maximum is about 50 C)

SMOOTHING_WINDOW = 5
WARMUP_SECONDS = 120
HOLD_SECONDS = 10
MAX_BAD_LINES = 5
NO_DATA_SECONDS = 5

LEVEL_NAMES = ["SAFE", "WARNING", "POSSIBLE FIRE", "FIRE DETECTED"]

LED_OFF, LED_ON, LED_BLINK, LED_BLINK_FAST = 0, 1, 2, 3
BUZZ_OFF, BUZZ_SLOW, BUZZ_FAST, BUZZ_CONTINUOUS = 0, 1, 2, 3


@dataclass
class Reading:
    smoke: float
    flame: float
    temp: Optional[float]
    hum: Optional[float]


@dataclass
class Command:
    level: int
    red: int
    green: int
    buzzer: int
    fan: int
    line1: str
    line2: str


def parse_line(line: str) -> Optional[Reading]:
    parts = line.strip().split(",")
    if len(parts) != 5 or parts[0] != "D":
        return None
    try:
        smoke = float(parts[1])
        flame = float(parts[2])
        temp = None if parts[3].lower() == "nan" else float(parts[3])
        hum = None if parts[4].lower() == "nan" else float(parts[4])
    except ValueError:
        return None
    if not (0 <= smoke <= 1023 and 0 <= flame <= 1023):
        return None
    return Reading(smoke, flame, temp, hum)


class Smoother:

    def __init__(self, size: int = SMOOTHING_WINDOW):
        self.smoke = deque(maxlen=size)
        self.flame = deque(maxlen=size)

    def add(self, r: Reading) -> Reading:
        self.smoke.append(r.smoke)
        self.flame.append(r.flame)
        return Reading(
            sum(self.smoke) / len(self.smoke),
            sum(self.flame) / len(self.flame),
            r.temp,
            r.hum,
        )


def classify(r: Reading):
    smoke_high = r.smoke > SMOKE_HIGH
    flame_high = r.flame > FLAME_HIGH
    temp_state = 0                      # 0 normal, 1 warning, 2 critical
    if r.temp is not None:
        if r.temp >= TEMP_CRITICAL:
            temp_state = 2
        elif r.temp > TEMP_HIGH:
            temp_state = 1
    return smoke_high, flame_high, temp_state


def decide_level(smoke_high: bool, flame_high: bool, temp_state: int) -> int:
    return min(3, int(smoke_high) + int(flame_high) + int(temp_state))


class LevelHolder:

    def __init__(self, hold_seconds: float = HOLD_SECONDS):
        self.hold = hold_seconds
        self.level = 0
        self.last_high_time = 0.0

    def update(self, new_level: int, now: float) -> int:
        if new_level >= self.level:
            self.level = new_level
            self.last_high_time = now
        elif now - self.last_high_time >= self.hold:
            self.level = new_level
        return self.level


def sensor_names(smoke_high: bool, flame_high: bool, temp_state: int) -> str:
    names = []
    if smoke_high:
        names.append("Smoke")
    if flame_high:
        names.append("Flame")
    if temp_state:
        names.append("Heat")
    return "+".join(names)


def plan_outputs(level: int, r: Reading, flags) -> Command:
    # Only a red and a green LED are fitted, so the levels use blink speed.
    temp_text = "--" if r.temp is None else f"{r.temp:.0f}"
    live = f"S{r.smoke:.0f} F{r.flame:.0f} T{temp_text}"
    if level == 0:
        return Command(0, LED_OFF, LED_ON, BUZZ_OFF, 0, "SAFE", live)
    if level == 1:
        return Command(1, LED_BLINK, LED_OFF, BUZZ_SLOW, 0, "WARNING", live)
    if level == 2:
        return Command(2, LED_BLINK_FAST, LED_OFF, BUZZ_FAST, 0,
                       "POSSIBLE FIRE", live)
    return Command(3, LED_ON, LED_OFF, BUZZ_CONTINUOUS, 1,
                   "FIRE DETECTED", "Fan ON - leave!")


def live_text(r: Reading) -> str:
    temp_text = "--" if r.temp is None else f"{r.temp:.0f}"
    return f"S{r.smoke:.0f} F{r.flame:.0f} T{temp_text}"


def warmup_command(seconds_left: int, r: Reading) -> Command:
    # still show the real readings while the MQ-2 warms up
    return Command(0, LED_OFF, LED_BLINK, BUZZ_OFF, 0,
                   f"WARM-UP {seconds_left}s", live_text(r))


def fault_command(message: str) -> Command:
    return Command(0, LED_BLINK, LED_OFF, BUZZ_SLOW, 0, "SENSOR FAULT", message)


def clean_text(text: str) -> str:
    return text.replace(",", " ").replace("|", " ")[:16]


def format_command(c: Command) -> str:
    return (f"C,{c.level},{c.red},{c.green},{c.buzzer},{c.fan},"
            f"{clean_text(c.line1)}|{clean_text(c.line2)}\n")


def send(link, c: Command) -> None:
    link.write(format_command(c).encode("ascii", errors="replace"))


def open_port(port: str, baud: int = 9600):
    import serial
    link = serial.Serial(port, baud, timeout=2)
    time.sleep(2)
    return link


SCENARIOS = [
    (6, 120, 10, 28.0),
    (6, 650, 10, 28.0),
    (6, 120, 10, 28.0),
    (6, 120, 520, 28.0),
    (6, 120, 10, 28.0),
    (6, 120, 10, 42.0),
    (6, 120, 10, 28.0),
    (6, 650, 560, 28.0),
    (8, 700, 600, 52.0),
    (12, 120, 10, 28.0),
    (6, 120, 10, 47.0),
]


class SimulatedArduino:

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.script = [(s, f, t) for n, s, f, t in SCENARIOS for _ in range(n)]
        self.index = 0
        self.commands = []

    def readline(self) -> bytes:
        time.sleep(self.interval)
        if self.index >= len(self.script):
            raise EOFError("simulation finished")
        s, f, t = self.script[self.index]
        self.index += 1
        return f"D,{s},{f},{t},45.0\n".encode()

    def write(self, data: bytes) -> None:
        self.commands.append(data.decode())

    def close(self) -> None:
        pass


def run(link, warmup: float, hold: float, log_path: Optional[str], max_cycles=None):
    smoother = Smoother()
    holder = LevelHolder(hold)
    start = time.time()
    last_good = start
    bad_in_a_row = 0
    cycles = 0
    log_file = writer = None
    if log_path:
        log_file = open(log_path, "w", newline="")
        writer = csv.writer(log_file)
        writer.writerow(["time", "smoke", "flame", "temp", "hum",
                         "smoke_high", "flame_high", "temp_state", "level"])
    try:
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            try:
                raw = link.readline().decode(errors="ignore").strip()
            except EOFError:
                break
            now = time.time()

            reading = parse_line(raw) if raw else None
            if reading is None:
                bad_in_a_row += 1 if raw else 0
                if bad_in_a_row >= MAX_BAD_LINES:
                    send(link, fault_command("Bad data"))
                elif now - last_good > NO_DATA_SECONDS:
                    send(link, fault_command("No data"))
                continue
            bad_in_a_row = 0
            last_good = now

            if now - start < warmup:
                # The MQ-2 is not stable yet, so smoke is ignored here.
                # Flame and heat are still checked, so a real fire is not
                # missed during the warm-up (same as the Arduino's own rule).
                _, flame_high, temp_state = classify(reading)
                early = decide_level(False, flame_high, temp_state)
                level = holder.update(early, now)
                if level > 0:
                    send(link, plan_outputs(level, reading,
                                            (False, flame_high, temp_state)))
                    print(f"Warm-up alert: {LEVEL_NAMES[level]} | flame "
                          f"{reading.flame:.0f} temp {reading.temp}")
                else:
                    left = int(warmup - (now - start))
                    send(link, warmup_command(left, reading))
                    print(f"Warming up... {left}s left | smoke {reading.smoke:.0f} "
                          f"flame {reading.flame:.0f} temp {reading.temp}")
                continue

            smooth = smoother.add(reading)
            flags = classify(smooth)
            level = holder.update(decide_level(*flags), now)
            command = plan_outputs(level, smooth, flags)
            send(link, command)

            stamp = time.strftime("%H:%M:%S")
            tag = "".join(ch if on else "-" for ch, on in zip("SFT", flags))
            temp_text = "n/a" if smooth.temp is None else f"{smooth.temp:.1f}"
            print(f"{stamp} | smoke {smooth.smoke:5.0f} flame {smooth.flame:5.0f} "
                  f"temp {temp_text:>5} | {tag} | {LEVEL_NAMES[level]}")
            if writer:
                writer.writerow([stamp, round(smooth.smoke), round(smooth.flame), smooth.temp,
                                 smooth.hum, *map(int, flags), level])
                log_file.flush()
    finally:
        if log_file:
            log_file.close()


def calibrate(link, seconds: int = 30) -> None:
    print(f"Calibration: keep the room in its NORMAL state for {seconds} s ...")
    values = {"smoke": [], "flame": [], "temp": []}
    end = time.time() + seconds
    while time.time() < end:
        r = parse_line(link.readline().decode(errors="ignore"))
        if r is None:
            continue
        values["smoke"].append(r.smoke)
        values["flame"].append(r.flame)
        if r.temp is not None:
            values["temp"].append(r.temp)
        print(f"  smoke {r.smoke:.0f}  flame {r.flame:.0f}  temp {r.temp}")
    for name, data in values.items():
        if data:
            print(f"{name:6s} min {min(data):7.1f}  avg {sum(data) / len(data):7.1f}  max {max(data):7.1f}")
    print("Set each threshold clearly above the normal maximum, then test with a real source.")


def main():
    ap = argparse.ArgumentParser(description="AgniSense fire monitor")
    ap.add_argument("--port", help="serial port, for example COM3 or /dev/ttyUSB0")
    ap.add_argument("--simulate", action="store_true", help="run without hardware")
    ap.add_argument("--calibrate", action="store_true", help="measure normal readings")
    ap.add_argument("--warmup", type=float, default=WARMUP_SECONDS, help="warm-up seconds")
    ap.add_argument("--hold", type=float, default=HOLD_SECONDS, help="alert hold seconds")
    ap.add_argument("--log", default="fire_log.csv", help="CSV log file ('' to switch off)")
    args = ap.parse_args()

    if args.simulate:
        link = SimulatedArduino()
        args.warmup = min(args.warmup, 2)
        args.hold = min(args.hold, 3)
    elif args.port:
        link = open_port(args.port)
    else:
        sys.exit("Give --port COM3 (or similar), or use --simulate.")

    try:
        if args.calibrate:
            calibrate(link)
        else:
            run(link, args.warmup, args.hold, args.log or None)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        link.close()


if __name__ == "__main__":
    main()
