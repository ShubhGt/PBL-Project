/*
  AgniSense - Arduino Nano firmware (your sketch + Python link)

  Works on its own, and also with fire_monitor.py on the laptop.
  If Python is connected, Python decides the level and the Arduino shows it.
  If Python is silent for 5 s (or not connected), the Arduino decides by itself.

  Arduino -> Python (every 1 s):  D,smoke,flame,temp,hum
  Python  -> Arduino:             C,level,red,green,buzzer,fan,line1|line2
    LED codes   : 0 off, 1 on, 2 blink slow, 3 blink fast
    Buzzer codes: 0 off, 1 slow beeps, 2 fast beeps, 3 continuous
    Fan code    : 0 off, 1 on
*/

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>
#include <SoftwareSerial.h>

// ---- Pins ----
#define MQ2_PIN     A0
#define FLAME_PIN   2
#define DHT_PIN     3
#define BUZZER_PIN  8
#define RED_LED     9
#define GREEN_LED   6
#define RELAY_PIN   7

// ---- Settings you can change ----
int   SMOKE_LIMIT = 400;            // 0 to 1023, tune using Serial Monitor
float TEMP_WARN   = 40.0;           // degrees C: above this = 1 point (WARNING)
float TEMP_LIMIT  = 45.0;           // degrees C: this or more = 2 points (DHT11 max is about 50 C)
const bool RELAY_ACTIVE_LOW = true; // set false if the relay works in reverse
const unsigned long WARMUP_MS         = 20000UL; // MQ-2 warm-up when running alone (use 120000 for real use)
const unsigned long PYTHON_TIMEOUT_MS = 5000UL;  // no command for this long = local mode
const bool DEBUG_TEXT = false;      // true prints readable lines, but DO NOT use with Python

DHT dht(DHT_PIN, DHT11);
LiquidCrystal_I2C lcd(0x27, 16, 2); // try 0x3F if the screen stays blank
SoftwareSerial bt(10, 11);          // RX = D10, TX = D11

const char* names[] = {"SAFE", "WARNING", "POSSIBLE FIRE", "FIRE DETECTED"};
int level = 0, lastLevel = -1;
float temperature = 0, humidity = 0;
bool haveTemp = false, haveHum = false;
unsigned long lastSensor = 0, lastDht = 0, lastBt = 0, lastCmdTime = 0;
int sensorTick = 0;

// what the outputs should do right now
int  redMode = 0, greenMode = 1, buzzerMode = 0;
bool fanOn = false;

// command received from Python
int  cmdLevel = 0, cmdRed = 0, cmdGreen = 0, cmdBuzzer = 0, cmdFan = 0;
char cmdLine1[17] = "", cmdLine2[17] = "";
char rxBuf[64];
byte rxLen = 0;

void setRelay(bool on) {
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? !on : on);
}

void showLine(int row, String text) {
  while (text.length() < 16) text += ' ';
  lcd.setCursor(0, row);
  lcd.print(text.substring(0, 16));
}

void writeLed(int pin, int mode) {
  bool on = (mode == 1)
         || (mode == 2 && ((millis() / 500) % 2 == 0))   // slow blink
         || (mode == 3 && ((millis() / 200) % 2 == 0));  // fast blink
  digitalWrite(pin, on);
}

void updateBuzzer() {
  if (buzzerMode == 0) { digitalWrite(BUZZER_PIN, LOW); return; }
  if (buzzerMode == 3) { digitalWrite(BUZZER_PIN, HIGH); return; }
  // 1 = WARNING: clear beep every second, 2 = POSSIBLE FIRE: fast beeps
  unsigned long onTime = (buzzerMode == 1) ? 300 : 150;
  unsigned long period = (buzzerMode == 1) ? 1000 : 300;
  digitalWrite(BUZZER_PIN, (millis() % period) < onTime);
}

// ---- Python link ----
void parseCommand(char* s) {
  // s looks like: C,3,1,0,3,1,FIRE DETECTED|Fan ON - leave!
  if (s[0] != 'C' || s[1] != ',') return;
  char* p = s + 2;
  int v[5];
  for (int i = 0; i < 5; i++) {
    v[i] = atoi(p);
    p = strchr(p, ',');
    if (!p) return;                  // incomplete line: ignore it
    p++;
  }
  if (v[0] < 0 || v[0] > 3) return;
  cmdLevel = v[0]; cmdRed = v[1]; cmdGreen = v[2]; cmdBuzzer = v[3]; cmdFan = v[4];

  char* bar = strchr(p, '|');
  if (bar) {
    *bar = 0;
    strncpy(cmdLine1, p, 16);
    strncpy(cmdLine2, bar + 1, 16);
  } else {
    strncpy(cmdLine1, p, 16);
    cmdLine2[0] = 0;
  }
  cmdLine1[16] = 0;
  cmdLine2[16] = 0;
  lastCmdTime = millis();
}

void readSerialCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      rxBuf[rxLen] = 0;
      parseCommand(rxBuf);
      rxLen = 0;
    } else if (c != '\r') {
      if (rxLen < sizeof(rxBuf) - 1) rxBuf[rxLen++] = c;
      else rxLen = 0;                // too long: discard
    }
  }
}

void sendDataLine(int smoke, bool flame) {
  Serial.print("D,");
  Serial.print(smoke);
  Serial.print(',');
  Serial.print(flame ? 1023 : 0);    // digital flame sensor: 1023 = flame, 0 = none
  Serial.print(',');
  if (haveTemp) Serial.print(temperature, 1); else Serial.print("nan");
  Serial.print(',');
  if (haveHum) Serial.print(humidity, 1); else Serial.print("nan");
  Serial.print('\n');
}

void setup() {
  Serial.begin(9600);
  bt.begin(9600);                   // HC-05 default speed
  pinMode(FLAME_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(RED_LED, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(RELAY_PIN, OUTPUT);
  setRelay(false);

  dht.begin();
  lcd.init();
  lcd.backlight();
  showLine(0, "AgniSense");
  showLine(1, "Starting...");
}

void loop() {
  unsigned long now = millis();

  readSerialCommands();
  bool pythonActive = (lastCmdTime != 0) && (now - lastCmdTime <= PYTHON_TIMEOUT_MS);

  if (now - lastSensor >= 500) {
    lastSensor = now;

    if (now - lastDht >= (haveTemp ? 2000UL : 1000UL)) {   // DHT11 reads about every 2 s
      float t = dht.readTemperature();
      float h = dht.readHumidity();
      if (!isnan(t)) { temperature = t; haveTemp = true; }
      if (!isnan(h)) { humidity = h; haveHum = true; }
      lastDht = now;
    }

    int  smoke = analogRead(MQ2_PIN);
    bool flame = (digitalRead(FLAME_PIN) == LOW); // LOW when flame seen

    sensorTick++;
    if (sensorTick % 2 == 0) sendDataLine(smoke, flame);   // one line per second for Python

    if (pythonActive) {
      // ---- Python is in charge ----
      level = cmdLevel;
      redMode = cmdRed; greenMode = cmdGreen; buzzerMode = cmdBuzzer;
      fanOn = (cmdFan == 1);
      showLine(0, String(cmdLine1));
      showLine(1, String(cmdLine2));
    } else {
      // ---- Arduino decides alone (your original logic, with two temperature steps) ----
      bool warming   = now < WARMUP_MS;
      bool smokeHigh = !warming && (smoke > SMOKE_LIMIT);
      int  tempPoints = 0;
      if (temperature >= TEMP_LIMIT) tempPoints = 2;
      else if (temperature > TEMP_WARN) tempPoints = 1;

      level = (int)flame + (int)smokeHigh + tempPoints;
      if (level > 3) level = 3;

      greenMode = (level == 0) ? 1 : 0;
      // same LED pattern as Python: slow blink, fast blink, then steady
      redMode   = (level == 0) ? 0 : (level == 1) ? 2 : (level == 2) ? 3 : 1;
      buzzerMode = level;            // 0 off, 1 slow, 2 fast, 3 continuous
      fanOn = smokeHigh || level >= 2;   // fan extracts smoke

      if (warming && level == 0) {
        // show the real readings during warm-up too
        showLine(0, "Warm-up " + String((WARMUP_MS - now) / 1000UL + 1) + "s");
        showLine(1, "T:" + String((int)temperature) + "C S:" + String(smoke) +
                    " F:" + String(flame ? 1 : 0));
      } else {
        showLine(0, names[level]);
        showLine(1, "T:" + String((int)temperature) + "C S:" + String(smoke) +
                    " F:" + String(flame ? 1 : 0));
      }
    }

    setRelay(fanOn);

    if (DEBUG_TEXT) {
      Serial.print("Smoke: "); Serial.print(smoke);
      Serial.print("  Temp: "); Serial.print(temperature);
      Serial.print("  Flame: "); Serial.println(flame);
    }

    // Send to phone when level changes, and every 5 seconds
    if (level != lastLevel || now - lastBt >= 5000) {
      bt.print("STATE: "); bt.print(names[level]);
      bt.print(" | T:"); bt.print(temperature);
      bt.print("C | Smoke:"); bt.print(smoke);
      bt.print(" | Flame:"); bt.println(flame ? "YES" : "NO");
      lastLevel = level;
      lastBt = now;
    }
  }

  if (redMode != 0) greenMode = 0;   // red and green are never on together
  writeLed(GREEN_LED, greenMode);
  writeLed(RED_LED, redMode);
  updateBuzzer();
}
