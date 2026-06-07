// ==========================================
// Gemma 4 -> Arduino -> 双步进电机
// 接收格式：LEVEL:0、LEVEL:1、LEVEL:2、LEVEL:3
// ==========================================

const int PUL_A = 7;
const int DIR_A = 6;
const int ENA_A = 5;

const int PUL_B = 10;
const int DIR_B = 9;
const int ENA_B = 8;

// 如果 MOTOR:ON 但电机不转，将 HIGH 改成 LOW。
const int DRIVER_ENABLE_LEVEL = HIGH;

// 超过 20 秒没有收到 Python 数据，停止电机。
const unsigned long DATA_TIMEOUT_MS = 20000;

String inputLine = "";

int currentLevel = 0;
bool hasReceivedData = false;
bool motorEnabled = false;
bool pulseState = false;

float phase = 0.0;
float breath = 0.0;

unsigned long pulseDelayMicros = 900;
unsigned long lastDataTime = 0;
unsigned long lastPulseToggleMicros = 0;
unsigned long lastPhaseUpdateMicros = 0;
unsigned long lastPrintTime = 0;


// ------------------------------------------
// 初始化
// ------------------------------------------
void setup() {
  pinMode(PUL_A, OUTPUT);
  pinMode(DIR_A, OUTPUT);
  pinMode(ENA_A, OUTPUT);

  pinMode(PUL_B, OUTPUT);
  pinMode(DIR_B, OUTPUT);
  pinMode(ENA_B, OUTPUT);

  digitalWrite(PUL_A, LOW);
  digitalWrite(PUL_B, LOW);

  // 两个电机反方向旋转。
  // 希望同方向时，将 DIR_B 改为 LOW。
  digitalWrite(DIR_A, LOW);
  digitalWrite(DIR_B, HIGH);

  setMotorEnabled(false);

  Serial.begin(115200);
  Serial.println("READY");
  Serial.println("Waiting for LEVEL:0 to LEVEL:3");

  lastPhaseUpdateMicros = micros();
}


// ------------------------------------------
// 主循环
// ------------------------------------------
void loop() {
  readLevelFromSerial();
  updateMotor();
  printStatus();
}


// ------------------------------------------
// 接收 Python 发送的 LEVEL
// ------------------------------------------
void readLevelFromSerial() {
  while (Serial.available() > 0) {
    char incomingChar = Serial.read();

    if (incomingChar == '\n') {
      inputLine.trim();

      if (inputLine.startsWith("LEVEL:")) {
        int receivedLevel = inputLine.substring(6).toInt();

        if (receivedLevel >= 0 && receivedLevel <= 3) {
          currentLevel = receivedLevel;
          lastDataTime = millis();
          hasReceivedData = true;
        }
      }

      inputLine = "";
    }
    else if (incomingChar != '\r') {
      if (inputLine.length() < 40) {
        inputLine += incomingChar;
      }
    }
  }
}


// ------------------------------------------
// 非阻塞电机控制
// ------------------------------------------
void updateMotor() {
  bool dataIsFresh =
    hasReceivedData &&
    (millis() - lastDataTime <= DATA_TIMEOUT_MS);

  if (!dataIsFresh) {
    setMotorEnabled(false);
    return;
  }

  setMotorEnabled(true);

  unsigned long nowMicros = micros();

  float elapsedSeconds =
    (nowMicros - lastPhaseUpdateMicros) / 1000000.0;

  lastPhaseUpdateMicros = nowMicros;

  if (elapsedSeconds > 0.1) {
    elapsedSeconds = 0.1;
  }

  // 等级越高，呼吸越急促。
  float breathHz = 0.20;

  if (currentLevel == 1) {
    breathHz = 0.45;
  }
  else if (currentLevel == 2) {
    breathHz = 0.85;
  }
  else if (currentLevel == 3) {
    breathHz = 1.35;
  }

  phase += 2.0 * PI * breathHz * elapsedSeconds;
  breath = (sin(phase) + 1.0) * 0.5;

  int baseDelay = 1200;

  if (currentLevel == 1) {
    baseDelay = 950;
  }
  else if (currentLevel == 2) {
    baseDelay = 720;
  }
  else if (currentLevel == 3) {
    baseDelay = 520;
  }

  pulseDelayMicros =
    baseDelay - (unsigned long)(breath * 350.0);

  if (pulseDelayMicros < 140) {
    pulseDelayMicros = 140;
  }

  if (
    nowMicros - lastPulseToggleMicros
    >= pulseDelayMicros
  ) {
    lastPulseToggleMicros = nowMicros;

    pulseState = !pulseState;

    digitalWrite(PUL_A, pulseState);
    digitalWrite(PUL_B, pulseState);
  }
}


// ------------------------------------------
// TB6600 使能
// ------------------------------------------
void setMotorEnabled(bool enabled) {
  if (enabled == motorEnabled) {
    return;
  }

  motorEnabled = enabled;

  int outputLevel =
    enabled ? DRIVER_ENABLE_LEVEL : !DRIVER_ENABLE_LEVEL;

  digitalWrite(ENA_A, outputLevel);
  digitalWrite(ENA_B, outputLevel);

  if (!enabled) {
    digitalWrite(PUL_A, LOW);
    digitalWrite(PUL_B, LOW);
    pulseState = false;
  }
}


// ------------------------------------------
// 输出状态
// ------------------------------------------
void printStatus() {
  if (millis() - lastPrintTime < 1000) {
    return;
  }

  lastPrintTime = millis();

  Serial.print("LEVEL:");
  Serial.print(currentLevel);

  Serial.print(",BREATH:");
  Serial.print(breath, 3);

  Serial.print(",DELAY:");
  Serial.print(pulseDelayMicros);

  Serial.print(",MOTOR:");
  Serial.println(motorEnabled ? "ON" : "OFF");
}