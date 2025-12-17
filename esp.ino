// rover_serial.ino
// ESP32 code: ultrasonic telemetry + serial JSON command listener
// Requirements: ArduinoJson library (6.x)

#include <Arduino.h>
#include <ArduinoJson.h>

// Motor connections
const int ENA = 25;
const int IN1 = 26;
const int IN2 = 27;

const int ENB = 13;
const int IN3 = 14;
const int IN4 = 12;

// Motor polarity
const int LEFT_MOTOR_POLARITY  = 1;
const int RIGHT_MOTOR_POLARITY = -1;

// Ultrasonic pins
const int TRIG_F = 5;
const int ECHO_F = 18;

const int TRIG_L = 4;
const int ECHO_L = 19;

const int TRIG_R = 15;
const int ECHO_R = 23;

const float OBSTACLE_LIMIT = 20.0;   // cm

// Control state
bool remoteOverride = false;
String remoteAction = ""; // "forward", "backward", "left", "right", "stop"
String currentMode = "auto";

unsigned long lastSerialCheck = 0;
const unsigned long SERIAL_CHECK_INTERVAL = 50; // ms

// Forward declarations
float getDistance(int trigPin, int echoPin);
void setLeft(bool f);
void setRight(bool f);
void moveForward();
void moveBackward();
void turnLeft();
void turnRight();
void stopMotors();
void publishTelemetry(float F, float L, float R);
void handleSerialCommands();

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);

  pinMode(ENB, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  pinMode(TRIG_F, OUTPUT); pinMode(ECHO_F, INPUT);
  pinMode(TRIG_L, OUTPUT); pinMode(ECHO_L, INPUT);
  pinMode(TRIG_R, OUTPUT); pinMode(ECHO_R, INPUT);

  digitalWrite(ENA, HIGH);
  digitalWrite(ENB, HIGH);

  Serial.println("3-US Rover Ready!");
}

void loop() {
  // read sensors
  float F = getDistance(TRIG_F, ECHO_F);
  float L = getDistance(TRIG_L, ECHO_L);
  float R = getDistance(TRIG_R, ECHO_R);

  // print telemetry line for the Pi (parseable)
  publishTelemetry(F, L, R);

  // check serial commands periodically
  if (millis() - lastSerialCheck >= SERIAL_CHECK_INTERVAL) {
    lastSerialCheck = millis();
    handleSerialCommands();
  }

  // decide motion
  if (remoteOverride) {
    if (remoteAction == "forward") moveForward();
    else if (remoteAction == "backward") moveBackward();
    else if (remoteAction == "left") turnLeft();
    else if (remoteAction == "right") turnRight();
    else if (remoteAction == "stop") stopMotors();
    else stopMotors();
  } else {
    // autonomous fallback obstacle avoidance
    if (F < OBSTACLE_LIMIT) {
      stopMotors();
      delay(100);
      if (L > R) {
        Serial.println("Turning LEFT");
        turnLeft();
        delay(400);
      } else {
        Serial.println("Turning RIGHT");
        turnRight();
        delay(400);
      }
    } else {
      moveForward();
    }
  }

  delay(40);
}

float getDistance(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW); delayMicroseconds(2);
  digitalWrite(trigPin, HIGH); delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  unsigned long dur = pulseIn(echoPin, HIGH, 30000);
  if (dur == 0) return 999;
  return dur * 0.0343 / 2.0;
}

void setLeft(bool f) {
  bool phys = (LEFT_MOTOR_POLARITY == 1) ? f : !f;
  digitalWrite(IN1, phys ? HIGH : LOW);
  digitalWrite(IN2, phys ? LOW : HIGH);
}
void setRight(bool f) {
  bool phys = (RIGHT_MOTOR_POLARITY == 1) ? f : !f;
  digitalWrite(IN3, phys ? HIGH : LOW);
  digitalWrite(IN4, phys ? LOW : HIGH);
}

void moveForward()  { setLeft(true);  setRight(true);  }
void moveBackward() { setLeft(false); setRight(false); }
void turnLeft()     { setLeft(false); setRight(true); }
void turnRight()    { setLeft(true);  setRight(false); }
void stopMotors() {
  digitalWrite(IN1,LOW); digitalWrite(IN2,LOW);
  digitalWrite(IN3,LOW); digitalWrite(IN4,LOW);
}

void publishTelemetry(float F, float L, float R) {
  // Format telemetry as a compact JSON-like line for the Pi to parse:
  // T|F:12.3|L:34.1|R:56.2
  char buf[128];
  snprintf(buf, sizeof(buf), "T|F:%.2f|L:%.2f|R:%.2f", F, L, R);
  Serial.println(buf);
}

// Serial command handling: expects JSON on a single line, e.g.
// {"mode":"manual","action":"forward","params":{}}
void handleSerialCommands() {
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;

    // allow simple text commands as well
    if (line.equalsIgnoreCase("auto")) {
      currentMode = "auto";
      remoteOverride = false;
      Serial.println("MODE:auto");
      return;
    }
    if (line.equalsIgnoreCase("manual")) {
      currentMode = "manual";
      remoteOverride = true;
      Serial.println("MODE:manual");
      return;
    }

    // If line starts with { try parse JSON manually (lightweight)
    if (line.startsWith("{")) {
      // Use a fragile but simple parse: look for "action":"..."
      int a1 = line.indexOf("\"action\"");
      if (a1 >= 0) {
        int col = line.indexOf(':', a1);
        int q1 = line.indexOf('"', col);
        int q2 = line.indexOf('"', q1 + 1);
        if (q1 >= 0 && q2 > q1) {
          String action = line.substring(q1 + 1, q2);
          action.toLowerCase();
          remoteAction = action;
          remoteOverride = true;
          currentMode = "manual";
          Serial.print("REMOTE_ACTION:");
          Serial.println(remoteAction);
          continue;
        }
      }
    }

    // fallback: simple command words
    String l = line;
    l.toLowerCase();
    if (l.indexOf("forward") >= 0) {
      remoteAction = "forward"; remoteOverride = true; currentMode="manual";
      Serial.println("REMOTE_ACTION:forward");
    } else if (l.indexOf("back") >= 0) {
      remoteAction = "backward"; remoteOverride = true; currentMode="manual";
      Serial.println("REMOTE_ACTION:backward");
    } else if (l.indexOf("left") >= 0) {
      remoteAction = "left"; remoteOverride = true; currentMode="manual";
      Serial.println("REMOTE_ACTION:left");
    } else if (l.indexOf("right") >= 0) {
      remoteAction = "right"; remoteOverride = true; currentMode="manual";
      Serial.println("REMOTE_ACTION:right");
    } else if (l.indexOf("stop") >= 0) {
      remoteAction = "stop"; remoteOverride = true; currentMode="manual";
      Serial.println("REMOTE_ACTION:stop");
    }
  }
}
