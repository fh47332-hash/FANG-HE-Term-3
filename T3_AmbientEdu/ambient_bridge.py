from __future__ import annotations

import json
import statistics
import threading
import time
from collections import deque
from typing import Any

import requests
import serial
from flask import Flask, jsonify, request
from flask_cors import CORS


# ======================================================
# 基础配置
# ======================================================

ARDUINO_PORT = "/dev/cu.usbmodem64E8336259582"
BAUD_RATE = 115200

# 使用你已经成功运行的模型名称。
MODEL_NAME = "gemma4:e2b"

OLLAMA_API = "http://localhost:11434/api/chat"

# 网页每秒传入一次数据。
# Gemma 4 每隔 10 秒分析一次。
ANALYSIS_INTERVAL_SECONDS = 10

# 超过 8 秒没有收到网页数据，自动停止电机。
SENSOR_TIMEOUT_SECONDS = 8

# 保存最近 30 个数据点，约等于最近 30 秒。
sensor_history: deque[dict[str, float]] = deque(maxlen=30)

latest_sensor_data: dict[str, float] | None = None

data_lock = threading.Lock()
serial_lock = threading.Lock()

stop_event = threading.Event()

app = Flask(__name__)

# 测试阶段允许网页访问本地 Python 接口。
CORS(app)


# ======================================================
# Arduino 串口
# ======================================================

print("Connecting to Arduino...")

arduino = serial.Serial(
    ARDUINO_PORT,
    BAUD_RATE,
    timeout=1,
)

# Arduino 打开串口时可能重新启动。
time.sleep(2)

print("Arduino connected.")


def send_to_arduino(command: str) -> None:
    """
    通过 USB 串口向 Arduino 发送一行命令。
    """

    with serial_lock:
        message = f"{command}\n"

        arduino.write(
            message.encode("utf-8")
        )

        arduino.flush()

    print("Sent to Arduino:", command)


# ======================================================
# 数据清洗
# ======================================================

def read_number(
    payload: dict[str, Any],
    key: str,
) -> float:
    """
    确保网页传来的数据是数字。
    """

    if key not in payload:
        raise ValueError(
            f"Missing field: {key}"
        )

    value = float(payload[key])

    if value < -100000 or value > 1000000:
        raise ValueError(
            f"Invalid value for {key}: {value}"
        )

    return value


def clean_sensor_payload(
    payload: dict[str, Any],
) -> dict[str, float]:
    """
    读取网页中的六项空气数据。
    """

    return {
        "pm25": read_number(payload, "pm25"),
        "co2": read_number(payload, "co2"),
        "temp": read_number(payload, "temp"),
        "humidity": read_number(
            payload,
            "humidity",
        ),
        "voc": read_number(payload, "voc"),
        "nox": read_number(payload, "nox"),
        "timestamp": time.time(),
    }


# ======================================================
# 汇总最近 30 秒数据
# ======================================================

def summarize_one_metric(
    samples: list[dict[str, float]],
    key: str,
) -> dict[str, float]:
    values = [
        sample[key]
        for sample in samples
    ]

    return {
        "current": round(values[-1], 2),
        "average": round(
            statistics.mean(values),
            2,
        ),
        "minimum": round(min(values), 2),
        "maximum": round(max(values), 2),
        "trend": round(
            values[-1] - values[0],
            2,
        ),
    }


def summarize_sensor_data(
    samples: list[dict[str, float]],
) -> dict[str, Any]:
    return {
        "window_seconds": len(samples),
        "pm25_ug_m3": summarize_one_metric(
            samples,
            "pm25",
        ),
        "co2_ppm": summarize_one_metric(
            samples,
            "co2",
        ),
        "temperature_c": summarize_one_metric(
            samples,
            "temp",
        ),
        "humidity_percent": summarize_one_metric(
            samples,
            "humidity",
        ),
        "voc_index": summarize_one_metric(
            samples,
            "voc",
        ),
        "nox_index": summarize_one_metric(
            samples,
            "nox",
        ),
    }


# ======================================================
# Gemma 4 判断逻辑
# ======================================================

def ask_gemma4(
    summary: dict[str, Any],
) -> dict[str, Any]:
    """
    让 Gemma 4 根据六项数据，
    分别选择两个电机的速度档位。
    """

    schema = {
        "type": "object",
        "properties": {
            "motor_a_level": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
            },
            "motor_b_level": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
            },
            "motion_mode": {
                "type": "string",
                "enum": [
                    "calm",
                    "slightly_tense",
                    "restless",
                    "urgent",
                ],
            },
            "reason": {
                "type": "string",
            },
        },
        "required": [
            "motor_a_level",
            "motor_b_level",
            "motion_mode",
            "reason",
        ],
        "additionalProperties": False,
    }

    system_prompt = """
You control two stepper motors in an air-quality art installation.

Interpret all six recent sensor signals:
- PM2.5 in micrograms per cubic metre
- CO2 in ppm
- temperature in Celsius
- relative humidity in percent
- VOC index
- NOx index

Motor A represents pollution pressure, congestion, and tension.
Motor B represents recovery, openness, and calmness.

Choose one safe speed level for each motor:
- Level 0: slow
- Level 1: medium-slow
- Level 2: medium-fast
- Level 3: fast

Use the following provisional installation-specific guide.
These are artistic control thresholds, not medical thresholds.

Signals that increase pressure:
- PM2.5 rising or above approximately 35
- CO2 rising or above approximately 1200
- VOC index rising or above approximately 120
- NOx index rising or above approximately 120
- Temperature far outside approximately 18 to 28
- Humidity far outside approximately 35 to 70

Overall mapping:
- Stable or relatively calm air:
  motor_a_level = 0
  motor_b_level = 3

- Slightly tense air:
  motor_a_level = 1
  motor_b_level = 2

- Restless or clearly worsening air:
  motor_a_level = 2
  motor_b_level = 1

- Urgent or strongly worsening air:
  motor_a_level = 3
  motor_b_level = 0

Consider both current values and trends.
Never output a motor level below 0 or above 3.
Return only valid JSON matching the schema.
"""

    response = requests.post(
        OLLAMA_API,
        json={
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Analyse this sensor summary:\n"
                        + json.dumps(
                            summary,
                            ensure_ascii=False,
                        )
                    ),
                },
            ],
            "format": schema,
            "stream": False,
            "options": {
                "temperature": 0,
            },
        },
        timeout=120,
    )

    response.raise_for_status()

    response_json = response.json()

    result = json.loads(
        response_json["message"]["content"]
    )

    motor_a_level = int(
        result["motor_a_level"]
    )

    motor_b_level = int(
        result["motor_b_level"]
    )

    if not 0 <= motor_a_level <= 3:
        raise ValueError(
            "Invalid Motor A level."
        )

    if not 0 <= motor_b_level <= 3:
        raise ValueError(
            "Invalid Motor B level."
        )

    return result


# ======================================================
# 本地兜底规则
# ======================================================

def fallback_motor_levels(
    latest: dict[str, float],
) -> tuple[int, int]:
    """
    Gemma 4 暂时不可用时，
    根据六项数据计算压力分数。

    阈值仅用于装置测试，
    后续需要根据传感器实际数据重新调整。
    """

    pressure_score = 0

    if latest["pm25"] >= 35:
        pressure_score += 1

    if latest["co2"] >= 1200:
        pressure_score += 1

    if latest["voc"] >= 120:
        pressure_score += 1

    if latest["nox"] >= 120:
        pressure_score += 1

    if (
        latest["temp"] < 18
        or latest["temp"] > 28
    ):
        pressure_score += 1

    if (
        latest["humidity"] < 35
        or latest["humidity"] > 70
    ):
        pressure_score += 1

    if pressure_score <= 1:
        return 0, 3

    if pressure_score <= 2:
        return 1, 2

    if pressure_score <= 4:
        return 2, 1

    return 3, 0


# ======================================================
# 网页接口
# ======================================================

@app.post("/sensors")
def receive_sensor_data():
    global latest_sensor_data

    try:
        payload = request.get_json(
            force=True
        )

        cleaned = clean_sensor_payload(
            payload
        )

        with data_lock:
            latest_sensor_data = cleaned
            sensor_history.append(cleaned)

        return jsonify(
            {
                "ok": True,
            }
        )

    except Exception as error:
        return jsonify(
            {
                "ok": False,
                "error": str(error),
            }
        ), 400


@app.post("/stop")
def stop_motors():
    send_to_arduino("STOP")

    return jsonify(
        {
            "ok": True,
        }
    )


@app.get("/status")
def get_status():
    with data_lock:
        latest = latest_sensor_data

    return jsonify(
        {
            "ok": True,
            "latest_sensor_data": latest,
            "sample_count": len(
                sensor_history
            ),
        }
    )


# ======================================================
# 后台分析循环
# ======================================================

def analysis_worker() -> None:
    while not stop_event.wait(
        ANALYSIS_INTERVAL_SECONDS
    ):
        with data_lock:
            latest = latest_sensor_data
            samples = list(sensor_history)

        if latest is None or not samples:
            print(
                "Waiting for Dashboard sensor data..."
            )

            send_to_arduino("STOP")

            continue

        seconds_since_last_data = (
            time.time()
            - latest["timestamp"]
        )

        if (
            seconds_since_last_data
            > SENSOR_TIMEOUT_SECONDS
        ):
            print(
                "Dashboard data timed out. "
                "Stopping motors."
            )

            send_to_arduino("STOP")

            continue

        summary = summarize_sensor_data(
            samples
        )

        print(
            "Sending summary to Gemma 4:",
            json.dumps(
                summary,
                ensure_ascii=False,
            ),
        )

        try:
            result = ask_gemma4(
                summary
            )

            motor_a_level = int(
                result["motor_a_level"]
            )

            motor_b_level = int(
                result["motor_b_level"]
            )

            print(
                "Gemma 4 result:",
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            )

        except Exception as error:
            (
                motor_a_level,
                motor_b_level,
            ) = fallback_motor_levels(
                latest
            )

            print(
                "Gemma 4 unavailable. "
                "Using fallback rule. "
                f"Error: {error}"
            )

        send_to_arduino(
            f"MOTORS:{motor_a_level},"
            f"{motor_b_level}"
        )


# ======================================================
# 启动程序
# ======================================================

def main() -> None:
    worker = threading.Thread(
        target=analysis_worker,
        daemon=True,
    )

    worker.start()

    print("")
    print("Ambient bridge is running.")
    print("Dashboard endpoint:")
    print("http://127.0.0.1:8765/sensors")
    print("")
    print("Press Control + C to stop.")
    print("")

    try:
        app.run(
            host="127.0.0.1",
            port=8765,
            debug=False,
            use_reloader=False,
        )

    finally:
        stop_event.set()

        try:
            send_to_arduino("STOP")
        finally:
            arduino.close()

        print("Program stopped safely.")


if __name__ == "__main__":
    main()
