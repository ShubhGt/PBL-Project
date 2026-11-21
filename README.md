# AgniSense 🔥

A low-cost fire and smoke early warning system built with an Arduino Nano, three sensors and a Python program.

AgniSense decides whether there is a **real fire** by checking that more than one sensor agrees, so it gives fewer false alarms than a single smoke detector. It then warns people with LEDs, a buzzer, an LCD, a smoke-extracting fan and a Bluetooth message to a phone.

Built as a Project Based Learning (PBL) prototype for a B.Tech CSE (AI) course. Total hardware cost is about **₹1,480** (budget limit: ₹1,500).

> **Note:** this is a student prototype. It is not a certified fire alarm and must not replace one.

---

## How it works

```
Smoke, flame, heat  ->  Arduino Nano  --USB serial-->  Python on laptop
                         (reads sensors,                 (smooths readings,
                          drives outputs)  <--command--   decides alert level)
```

- **Arduino (C++)** reads the sensors every second, sends them to Python, and switches the LEDs, buzzer, fan relay and LCD.
- **Python** checks each data line, smooths the readings, counts how many sensors are high, holds the level steady, and sends back one command.
- **Fail-safe:** if Python is silent for 5 seconds (laptop off or unplugged), the Arduino decides by itself, so the alarm still works.

### Decision rule

Each sensor adds points. `level = min(3, total points)`.

| Sensor | Adds points when |
|---|---|
| MQ-2 smoke (0-1023) | above 400 → 1 point |
| IR flame (0-1023) | above 300 → 1 point |
| DHT11 temperature | above 40 °C → 1 point; 45 °C or more → 2 points |

| Level | Name | LED | Buzzer | Fan |
|---|---|---|---|---|
| 0 | SAFE | Green on | Off | Off |
| 1 | WARNING | Red blinks slowly | Slow beeps | Off |
| 2 | POSSIBLE FIRE | Red blinks fast | Fast beeps | Off |
| 3 | FIRE DETECTED | Red steady | Continuous | **On** |

Other states: **WARM-UP** (green blinks) and **SENSOR FAULT** (red blinks slowly, slow beeps).

Extra rules:
- **Smoothing:** smoke and flame use the average of the last 5 readings.
- **Hold rule:** the level rises at once but falls only after 10 seconds, so the alarm does not flicker.
- **Warm-up:** the MQ-2 needs about 120 seconds to settle. During this time smoke is ignored, but flame and heat are still checked.
- **Sensor fault:** 5 bad lines in a row, or no data for 5 seconds, shows SENSOR FAULT.

Because temperature of 45 °C or more counts as 2 points, **very high heat alone can reach POSSIBLE FIRE**. Any other single sensor gives at most WARNING.

---

## Hardware

| Part | Use |
|---|---|
| Arduino Nano | Microcontroller |
| MQ-2 | Smoke / gas sensor |
| IR flame sensor (digital) | Flame sensor |
| DHT11 | Temperature and humidity |
| Active buzzer | Audible warning |
| Red and green LEDs | Visual warning |
| 5V single relay + 5V DC fan | Smoke-extracting fan |
| 16x2 I2C LCD | Status text |
| HC-05 | Bluetooth message to phone |
| Resistors (1k / 2k), breadboard, jumper wires, 9V battery | Wiring and power |

### Pin connections (Arduino Nano)

| Pin | Connected to |
|---|---|
| A0 | MQ-2 analog output |
| D2 | Flame sensor digital output (LOW = flame) |
| D3 | DHT11 data |
| D6 | Green LED |
| D7 | Relay input |
| D8 | Buzzer |
| D9 | Red LED |
| D10 | HC-05 TX (Arduino RX) |
| D11 | HC-05 RX (Arduino TX) through a 1k / 2k voltage divider |
| A4 / A5 | LCD SDA / SCL |

If your relay works in reverse, set `RELAY_ACTIVE_LOW` to `false` in the sketch. If the LCD stays blank, change its address from `0x27` to `0x3F`.

---

## Setup

### 1. Arduino

1. Install these libraries in the Arduino IDE (Library Manager):
   - `LiquidCrystal I2C`
   - `DHT sensor library` (Adafruit) and `Adafruit Unified Sensor`
2. Open `firmware/AgniSense/AgniSense.ino`.
3. Select **Arduino Nano** and your port, then upload.

The sketch also works on its own, without Python.

### 2. Python

```bash
cd python
pip install -r ../requirements.txt
```

(Python 3.8 or newer. `pyserial` is only needed for real hardware.)

---

## Usage

Run from the `python/` folder.

```bash
# Try it without any hardware (built-in test scenarios)
python fire_monitor.py --simulate

# Measure your room's normal readings (to set the thresholds)
python fire_monitor.py --port COM3 --calibrate

# Run with the Arduino connected (use /dev/ttyUSB0 on Linux)
python fire_monitor.py --port COM3
```

Options:

| Option | Meaning | Default |
|---|---|---|
| `--port` | Serial port, for example `COM3` | none |
| `--simulate` | Run without hardware | off |
| `--calibrate` | Print min, average and max of normal readings | off |
| `--warmup` | MQ-2 warm-up in seconds | 120 |
| `--hold` | Seconds before the level may fall | 10 |
| `--log` | CSV log file (`''` turns logging off) | `fire_log.csv` |

Each cycle is printed and saved to the CSV log, which you can use to measure detection accuracy and false-alarm rate.

### Tuning the thresholds

The limits are starting values. Run `--calibrate`, then set each limit clearly above the normal maximum. The Python limits are at the top of `fire_monitor.py`. The Arduino-alone limits are at the top of `AgniSense.ino`. Keep the two the same.

---

## Serial protocol

| Direction | Line | Example |
|---|---|---|
| Arduino → Python (every 1 s) | `D,smoke,flame,temp,hum` | `D,312,0,28.0,45.0` |
| Python → Arduino | `C,level,red,green,buzzer,fan,line1\|line2` | `C,3,1,0,3,1,FIRE DETECTED\|Fan ON - leave!` |

Codes: LED `0` off, `1` on, `2` slow blink, `3` fast blink. Buzzer `0` off, `1` slow, `2` fast, `3` continuous. Fan `0` off, `1` on. The flame value is `1023` when flame is seen and `0` when not. A missing DHT11 reading is sent as `nan` and counts as "not high".

---

## Tests

```bash
cd python
python -m unittest -v
```

The tests cover line parsing, all sensor combinations, temperature levels, smoothing, the hold rule, LED and buzzer outputs, the warm-up behaviour, and a full simulated run.

---

## Known limits

- The DHT11 is slow and only accurate to about ±2 °C.
- The MQ-2 reacts to many gases (cooking fumes, spray), which is why a second sensor must agree before a fire is declared.
- The IR flame sensor can be fooled by strong sunlight.
- In Arduino-alone mode the fan also turns on when smoke is high or at level 2. With Python it turns on only at level 3.
- The prototype is not certified to any fire-safety standard.

---

## Sustainability

AgniSense supports UN SDGs 3 (Good Health and Well-being), 9 (Industry, Innovation and Infrastructure), 11 (Sustainable Cities and Communities) and 13 (Climate Action). Most parts can be reused or recycled, and non-recyclable parts should go to e-waste collection.

## Project documents

- `docs/AgniSense_Phase2_Methodology_Design.pdf`: problem decomposition, flowcharts and pseudocode. This is the earlier design; the code in this repo (two LEDs, two temperature steps) is the final version.
- `docs/EVS_EVAL.pptx`: survey, stakeholder analysis and sustainability presentation.

## Team

Add your team members' names here (only if everyone agrees).

## License

MIT. See [LICENSE](LICENSE).
