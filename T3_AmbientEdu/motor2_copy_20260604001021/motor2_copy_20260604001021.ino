// ======================================================
// LLM + Python + Arduino Stepper Motor Controller
//
// Python sends mote speed:
//   RPM:30,18
//   RPM:25,-20
//   STOP
//
// Motor A pins:
//   PUL_A = 7
//   DIR_A = 6
//   ENA_A = 5
//
// Motor B pins:
//   PUL_B = 10
//   DIR_B = 9
//   ENA_B = 8
//
// This version supports smooth acceleration / deceleration.
// ======================================================


// -------------------- Pin Settings --------------------
const int PUL_A = 7;
const int DIR_A = 6;
const int ENA_A = 5;

const int PUL_B = 10;
const int DIR_B = 9;
const int ENA_B = 8;


// -------------------- Driver Settings --------------------
// If the motor does not lock or move, try changing this to LOW.
const int DRIVER_ENABLE_LEVEL = HIGH;

// If direction is opposite, change these values.
const int DIR_A_FORWARD = LOW;
const int DIR_B_FORWARD = HIGH;


// -------------------- Motion Settings --------------------
// You must match this to your driver microstep setting.
// Example:
// full step: 200
// 1/2 step: 400
// 1/4 step: 800
// 1/8 step: 1600
// 1/16 step: 3200
//
// Your old driver table looked like it may use microstepping,
// so 1600 is a safe starting value.
const float STEPS_PER_REV = 1600.0;

// Safety limit. LLM/Python commands will be constrained to this range.
const float MAX_RPM = 120.0;

// How fast the motor speed changes.
// Larger = faster response, smaller = smoother motion.
const float RPM_ACCEL_PER_SECOND = 45.0;

// If no new command is received for 30 seconds, stop motors.
const unsigned long DATA_TIMEOUT_MS = 60000;


// -------------------- Serial --------------------
String inputLine = "";


// -------------------- Motor State --------------------
float targetRpmA = 0.0;
float targetRpmB = 0.0;

float currentRpmA = 0.0;
float currentRpmB = 0.0;

bool motorEnabled = false;
bool hasCommand = false;

bool pulseStateA = false;
bool pulseStateB = false;

unsigned long lastDataTime = 0;
unsigned long lastPulseA = 0;
unsigned long lastPulseB = 0;
unsigned long lastRampUpdate = 0;
unsigned long lastPrintTime = 0;


// ======================================================
// Setup
// ======================================================
void setup() {
  pinMode(PUL_A, OUTPUT);
  pinMode(DIR_A, OUTPUT);
  pinMode(ENA_A, OUTPUT);

  pinMode(PUL_B, OUTPUT);
  pinMode(DIR_B, OUTPUT);
  pinMode(ENA_B, OUTPUT);

  digitalWrite(PUL_A, LOW);
  digitalWrite(PUL_B, LOW);

  setMotorEnabled(false);

  Serial.begin(115200);

  lastRampUpdate = millis();

  Serial.println("READY");
  Serial.println("Send: RPM:30,18");
  Serial.println("Send: RPM:25,-20");
  Serial.println("Send: STOP");
}


// ======================================================
// Main Loop
// ======================================================
void loop() {
  readSerialCommand();
  updateSmoothSpeed();
  updateMotors();
  printStatus();
}


// ======================================================
// Read Serial Command
// ======================================================
void readSerialCommand() {
  while (Serial.available() > 0) {
    char incomingChar = Serial.read();

    if (incomingChar == '\n') {
      inputLine.trim();

      if (inputLine == "STOP") {
        targetRpmA = 0;
        targetRpmB = 0;
        hasCommand = false;

        Serial.println("STOP_RECEIVED");
      }

      else if (inputLine.startsWith("RPM:")) {
        String payload = inputLine.substring(4);
        int commaPosition = payload.indexOf(',');

        if (commaPosition > 0) {
          float receivedA = payload.substring(0, commaPosition).toFloat();
          float receivedB = payload.substring(commaPosition + 1).toFloat();

          targetRpmA = constrain(receivedA, -MAX_RPM, MAX_RPM);
          targetRpmB = constrain(receivedB, -MAX_RPM, MAX_RPM);

          lastDataTime = millis();
          hasCommand = true;

          setMotorEnabled(true);

          Serial.print("TARGET_RPM_A:");
          Serial.print(targetRpmA);
          Serial.print(",TARGET_RPM_B:");
          Serial.println(targetRpmB);
        }
      }

      inputLine = "";
    }

    else if (incomingChar != '\r') {
      if (inputLine.length() < 80) {
        inputLine += incomingChar;
      }
    }
  }
}


// ======================================================
// Smooth acceleration / deceleration
// ======================================================
void updateSmoothSpeed() {
  unsigned long now = millis();
  float dt = (now - lastRampUpdate) / 1000.0;

  if (dt <= 0) {
    return;
  }

  lastRampUpdate = now;

  bool commandIsFresh =
    hasCommand &&
    (now - lastDataTime <= DATA_TIMEOUT_MS);

  if (!commandIsFresh) {
    targetRpmA = 0;
    targetRpmB = 0;
  }

  currentRpmA = approachValue(
    currentRpmA,
    targetRpmA,
    RPM_ACCEL_PER_SECOND * dt
  );

  currentRpmB = approachValue(
    currentRpmB,
    targetRpmB,
    RPM_ACCEL_PER_SECOND * dt
  );

  if (
    abs(currentRpmA) < 0.05 &&
    abs(currentRpmB) < 0.05 &&
    abs(targetRpmA) < 0.05 &&
    abs(targetRpmB) < 0.05
  ) {
    currentRpmA = 0;
    currentRpmB = 0;
    setMotorEnabled(false);
  }
}


// ======================================================
// Move current value toward target value smoothly
// ======================================================
float approachValue(float currentValue, float targetValue, float maxStep) {
  if (currentValue < targetValue) {
    currentValue += maxStep;

    if (currentValue > targetValue) {
      currentValue = targetValue;
    }
  }

  else if (currentValue > targetValue) {
    currentValue -= maxStep;

    if (currentValue < targetValue) {
      currentValue = targetValue;
    }
  }

  return currentValue;
}


// ======================================================
// Update both motors
// ======================================================
void updateMotors() {
  if (!motorEnabled) {
    return;
  }

  updateOneMotor(
    PUL_A,
    DIR_A,
    DIR_A_FORWARD,
    pulseStateA,
    lastPulseA,
    currentRpmA
  );

  updateOneMotor(
    PUL_B,
    DIR_B,
    DIR_B_FORWARD,
    pulseStateB,
    lastPulseB,
    currentRpmB
  );
}


// ======================================================
// Generate pulses for one motor
// ======================================================
void updateOneMotor(
  int pulsePin,
  int dirPin,
  int forwardDirectionLevel,
  bool &pulseState,
  unsigned long &lastPulseTime,
  float rpm
) {
  if (abs(rpm) < 0.05) {
    digitalWrite(pulsePin, LOW);
    pulseState = false;
    return;
  }

  if (rpm >= 0) {
    digitalWrite(dirPin, forwardDirectionLevel);
  } else {
    digitalWrite(dirPin, !forwardDirectionLevel);
  }

  float stepsPerSecond = abs(rpm) * STEPS_PER_REV / 60.0;

  if (stepsPerSecond < 1) {
    return;
  }

  // We toggle HIGH/LOW, so pulse toggles twice per full step pulse.
  unsigned long toggleIntervalMicros =
    (unsigned long)(1000000.0 / (stepsPerSecond * 2.0));

  unsigned long nowMicros = micros();

  if (nowMicros - lastPulseTime >= toggleIntervalMicros) {
    lastPulseTime = nowMicros;

    pulseState = !pulseState;
    digitalWrite(pulsePin, pulseState);
  }
}


// ======================================================
// Enable / disable drivers
// ======================================================
void setMotorEnabled(bool enabled) {
  if (enabled == motorEnabled) {
    return;
  }

  motorEnabled = enabled;

  int outputLevel =
    enabled
      ? DRIVER_ENABLE_LEVEL
      : !DRIVER_ENABLE_LEVEL;

  digitalWrite(ENA_A, outputLevel);
  digitalWrite(ENA_B, outputLevel);

  if (!enabled) {
    digitalWrite(PUL_A, LOW);
    digitalWrite(PUL_B, LOW);

    pulseStateA = false;
    pulseStateB = false;
  }
}


// ======================================================
// Print status once per second
// ======================================================
void printStatus() {
  if (millis() - lastPrintTime < 1000) {
    return;
  }

  lastPrintTime = millis();

  Serial.print("CURRENT_RPM_A:");
  Serial.print(currentRpmA);

  Serial.print(",TARGET_RPM_A:");
  Serial.print(targetRpmA);

  Serial.print(",CURRENT_RPM_B:");
  Serial.print(currentRpmB);

  Serial.print(",TARGET_RPM_B:");
  Serial.print(targetRpmB);

  Serial.print(",MOTOR:");
  Serial.println(motorEnabled ? "ON" : "OFF");
}
