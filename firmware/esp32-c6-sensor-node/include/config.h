#pragma once

#ifndef IIOT_NODE_ID
#define IIOT_NODE_ID "node_01"
#endif

#ifndef IIOT_ROOM_ID
#define IIOT_ROOM_ID "room_A"
#endif

#ifndef IIOT_USE_MOCK_SENSORS
#define IIOT_USE_MOCK_SENSORS 1
#endif

namespace iiot {
constexpr unsigned long SAMPLE_INTERVAL_MS = 10000;
constexpr int MOVING_AVERAGE_SIZE = 3;

constexpr int I2C_SDA_PIN = 6;
constexpr int I2C_SCL_PIN = 7;
constexpr int SEN0377_ADC_CHANNEL = 0;

constexpr int LORA_RX_PIN = 16;
constexpr int LORA_TX_PIN = 17;
constexpr int LORA_AUX_PIN = 18;
constexpr int LORA_M0_PIN = 19;
constexpr int LORA_M1_PIN = 20;
constexpr int LORA_UART_PORT = 1;
constexpr int LORA_BAUD = 9600;
}
