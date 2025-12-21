// ----------------------------------------------------------
// ESP32 Rover – MANUAL + OBSTACLE SAFE (POLARITY FIXED)
// USB Serial controlled from Raspberry Pi
// ----------------------------------------------------------

// Motor connections
const int ENA = 25;
const int IN1 = 26;
const int IN2 = 27;

const int ENB = 13;
const int IN3 = 14;
const int IN4 = 12;

// ✅ RESTORED POLARITY (CRITICAL)
const int LEFT_MOTOR_POLARITY  = 1;     // do NOT change
const int RIGHT_MOTOR_POLARITY = -1;    // do NOT change

// Ultrasonic pins
const int TRIG_F = 5;
const int ECHO_F = 18;
const int TRIG_L = 4;
const int ECHO_L = 19;
const int TRIG_R = 15;
const int ECHO_R = 23;

const float OBSTACLE_LIMIT = 20.0;   // cm

String cmd = "";
bool manual_mode = false;

// ----------------------------------------------------------
void setup() {
  Serial.begin(115200);

  pinMode(ENA, OUTPUT); pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(ENB, OUTPUT); pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);

  pinMode(TRIG_F, OUTPUT); pinMode(ECHO_F, INPUT);
  pinMode(TRIG_L, OUTPUT); pinMode(ECHO_L, INPUT);
  pinMode(TRIG_R, OUTPUT); pinMode(ECHO_R, INPUT);

  digitalWrite(ENA, HIGH);
  digitalWrite(ENB, HIGH);

  Serial.println("ESP32 Rover Ready (Manual + Auto)");
}

// ----------------------------------------------------------
// MOTOR HELPERS (POLARITY SAFE)
// ----------------------------------------------------------
void setLeft(bool forward) {
  bool phys = (LEFT_MOTOR_POLARITY == 1) ? forward : !forward;
  digitalWrite(IN1, phys ? HIGH : LOW);
  digitalWrite(IN2, phys ? LOW  : HIGH);
}

void setRight(bool forward) {
  bool phys = (RIGHT_MOTOR_POLARITY == 1) ? forward : !forward;
  digitalWrite(IN3, phys ? HIGH : LOW);
  digitalWrite(IN4, phys ? LOW  : HIGH);
}

void moveForward()  { setLeft(true);  setRight(true);  }
void moveBackward() { setLeft(false); setRight(false); }
void turnLeft()     { setLeft(false); setRight(true);  }
void turnRight()    { setLeft(true);  setRight(false); }

void stopMotors() {
  digitalWrite(IN1, LOW); digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW); digitalWrite(IN4, LOW);
}

// ----------------------------------------------------------
float getDistance(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW); delayMicroseconds(2);
  digitalWrite(trigPin, HIGH); delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  unsigned long dur = pulseIn(echoPin, HIGH, 30000);
  if (dur == 0) return 999;
  return dur * 0.0343 / 2.0;
}

// ----------------------------------------------------------
void loop() {

  // -------- SERIAL COMMAND HANDLING --------
  if (Serial.available()) {
    cmd = Serial.readStringUntil('\n');
    cmd.trim();
    manual_mode = (cmd.length() > 0);
    Serial.println("CMD: " + cmd);
  }

  // -------- MANUAL MODE --------
  if (manual_mode) {
    if      (cmd == "forward")   moveForward();
    else if (cmd == "backward")  moveBackward();
    else if (cmd == "left")      turnLeft();
    else if (cmd == "right")     turnRight();
    else if (cmd == "stop") {
      stopMotors();
      manual_mode = false;   // return to auto
    }
    return;
  }

  // -------- AUTO OBSTACLE MODE --------
  float F = getDistance(TRIG_F, ECHO_F);
  float L = getDistance(TRIG_L, ECHO_L);
  float R = getDistance(TRIG_R, ECHO_R);

  if (F < OBSTACLE_LIMIT) {
    stopMotors();
    delay(100);
    if (L > R) turnLeft();
    else       turnRight();
    delay(400);
  } else {
    moveForward();
  }

  delay(40);
}