from __future__ import annotations

import json
import statistics
import time
from collections import deque
from typing import Any

import requests
import serial


# ==========================================
# 需要修改：填写你的 Arduino USB 串口
# ==========================================
ARDUINO_PORT = "/dev/cu.usbmodem64E8336259582"

# Arduino 串口速度，必须与 Arduino 程序一致。
BAUD_RATE = 115200

# 本地 Ollama 模型
MODEL_NAME = "gemma3:4b"

# Ollama 本地接口
OLLAMA_API = "http://localhost:11434/api/chat"

# 每隔多少秒让 Gemma 4 重新判断一次。
# 不要设置成 1 秒，否则模型可能跟不上。
ANALYSIS_INTERVAL_SECONDS = 10

# 暂时使用模拟 VOC 数据。
# 后续可以替换为真实 Dashboard 或传感器数据。
FAKE_VOC_DATA = [
    72, 74, 75, 77, 79,
    82, 84, 88, 91, 95,
    98, 102, 108, 115, 121,
    128, 132, 136, 140, 145,
    142, 138, 134, 129, 120,
    112, 105, 98, 92, 88,
    84, 80, 77, 75, 73,
]

# 保存最近 30 秒的数据。
recent_voc_values: deque[float] = deque(maxlen=30)


def calculate_features(values: list[float]) -> dict[str, float]:
    """将最近一段 VOC 数据转换成便于模型理解的摘要。"""

    if not values:
        raise ValueError("VOC data is empty.")

    first_value = values[0]
    last_value = values[-1]

    return {
        "current_voc": round(last_value, 2),
        "average_voc": round(statistics.mean(values), 2),
        "minimum_voc": round(min(values), 2),
        "maximum_voc": round(max(values), 2),
        "trend": round(last_value - first_value, 2),
        "sample_count": len(values),
    }


def fallback_level(current_voc: float) -> int:
    """
    本地兜底规则。
    当 Gemma 4 暂时无法回复时，仍然可以安全控制电机。
    这些阈值需要根据你的传感器实际输出重新校准。
    """

    if current_voc < 80:
        return 0

    if current_voc < 120:
        return 1

    if current_voc < 160:
        return 2

    return 3


def ask_gemma4(features: dict[str, float]) -> dict[str, Any]:
    """请求本地 Gemma 4 将数据转换为空气状态等级。"""

    schema = {
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
            },
            "motion_mode": {
                "type": "string",
                "enum": [
                    "calm",
                    "normal",
                    "restless",
                    "urgent",
                ],
            },
            "reason": {
                "type": "string",
            },
        },
        "required": [
            "level",
            "motion_mode",
            "reason",
        ],
    }

    system_prompt = """
You are an air-quality interpretation system for an art installation.

Your task is to translate recent VOC sensor readings into one of four
motor-motion levels.

Use these provisional thresholds as a guide:
- Level 0: calm, generally below 80
- Level 1: normal, generally from 80 to 119
- Level 2: restless, generally from 120 to 159
- Level 3: urgent, generally 160 or higher

Also consider the trend:
- A rapid rise may justify a higher level.
- A clear fall may justify a lower level.
- Never output a level outside 0 to 3.

Return only valid JSON that follows the schema.
"""

    user_prompt = (
        "Interpret the following recent VOC data summary:\n"
        + json.dumps(features, ensure_ascii=False)
    )

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
                    "content": user_prompt,
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
    content = response_json["message"]["content"]

    result = json.loads(content)

    level = int(result["level"])

    if level < 0 or level > 3:
        raise ValueError(f"Invalid level returned by model: {level}")

    return result


def send_level_to_arduino(
    arduino: serial.Serial,
    level: int,
) -> None:
    """向 Arduino 发送一行命令。"""

    message = f"LEVEL:{level}\n"
    arduino.write(message.encode("utf-8"))
    arduino.flush()

    print(f"Sent to Arduino: {message.strip()}")


def main() -> None:
    print("Connecting to Arduino...")

    with serial.Serial(
        ARDUINO_PORT,
        BAUD_RATE,
        timeout=1,
    ) as arduino:
        # Arduino 打开串口时可能会自动重启。
        time.sleep(2)

        print("Arduino connected.")
        print("Starting simulated VOC stream...")

        last_analysis_time = 0.0

        for voc in FAKE_VOC_DATA:
            voc = float(voc)
            recent_voc_values.append(voc)

            print(f"Current VOC: {voc:.1f}")

            now = time.time()

            if (
                now - last_analysis_time
                >= ANALYSIS_INTERVAL_SECONDS
            ):
                features = calculate_features(
                    list(recent_voc_values)
                )

                print(
                    "Sending summary to Gemma 4:",
                    json.dumps(
                        features,
                        ensure_ascii=False,
                    ),
                )

                try:
                    result = ask_gemma4(features)

                    level = int(result["level"])

                    print(
                        "Gemma 4 result:",
                        json.dumps(
                            result,
                            ensure_ascii=False,
                        ),
                    )

                except Exception as error:
                    level = fallback_level(voc)

                    print(
                        "Gemma 4 unavailable. "
                        f"Using fallback rule. Error: {error}"
                    )

                send_level_to_arduino(
                    arduino,
                    level,
                )

                last_analysis_time = now

            time.sleep(1)

        print("Finished simulated data stream.")


if __name__ == "__main__":
    main()
