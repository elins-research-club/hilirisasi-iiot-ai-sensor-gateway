#pragma once

#include <stdint.h>

#ifndef IIOT_USE_MOCK_SENSORS
#define IIOT_USE_MOCK_SENSORS 1
#endif

#ifndef IIOT_ENABLE_BOSCH_BME68X
#define IIOT_ENABLE_BOSCH_BME68X 0
#endif

namespace iiot {

constexpr const char* NODE_ID = "esp32c6_node_01";
constexpr const char* ROOM_ID = "room_A";
constexpr const char* GATEWAY_ID = "raspi_gateway_01";
constexpr uint32_t SAMPLE_INTERVAL_MS = 60000;

// Shared I2C bus. All addresses below are 7-bit addresses.
constexpr int I2C_PORT = 0;
constexpr int I2C_SDA_PIN = 6;
constexpr int I2C_SCL_PIN = 7;
constexpr uint32_t I2C_CLOCK_HZ = 100000;
constexpr uint8_t BME688_I2C_ADDRESS = 0x77;
constexpr uint8_t SEN0466_I2C_ADDRESS = 0x74;
constexpr uint8_t SEN0321_I2C_ADDRESS = 0x73;
constexpr uint8_t INA226_I2C_ADDRESS = 0x40;
constexpr uint8_t UART_BRIDGE_I2C_ADDRESS = 0x48;
constexpr uint32_t UART_BRIDGE_CRYSTAL_HZ = 1843200;

// SEN0574 is an analog qualitative lane. GPIO0 is deliberately different from
// the I2C SDA pin. Final PCB pin assignment still requires hardware sign-off.
constexpr int SEN0574_ADC_GPIO = 0;
static_assert(SEN0574_ADC_GPIO != I2C_SDA_PIN, "SEN0574 ADC must not share I2C SDA");

// Ebyte E32 remains on the only high-power UART used directly by the C6.
constexpr int LORA_UART_PORT = 1;
constexpr int LORA_TX_PIN = 16;
constexpr int LORA_RX_PIN = 17;
constexpr int LORA_M0_PIN = 18;
constexpr int LORA_M1_PIN = 19;
constexpr int LORA_AUX_PIN = 20;
constexpr int LORA_BAUD = 9600;
constexpr uint32_t LORA_AUX_TIMEOUT_MS = 3000;
constexpr uint32_t LORA_TX_TIMEOUT_MS = 5000;

// Concrete production UART strategy: keep E32 on UART1 and place MH-Z19B/C and
// PMS7003T on channels A/B of an external SC16IS752-compatible dual-UART bridge.
// Hardware mode fails closed when the bridge is absent. The bridge is a BOM and
// PCB gate; it is not silently emulated by the firmware.
constexpr uint32_t MHZ19_BAUD = 9600;
constexpr uint32_t PMS7003T_BAUD = 9600;
constexpr uint32_t MHZ19_WARMUP_MS = 180000;
constexpr uint32_t PMS7003T_WARMUP_MS = 30000;
constexpr uint32_t SENSOR_UART_TIMEOUT_MS = 2000;

// INA226 conversion uses an explicit current LSB. This must be recalculated from
// the actual shunt resistor and maximum expected current before hardware use.
constexpr float INA226_CURRENT_LSB_MA = 0.1F;
constexpr uint16_t INA226_CALIBRATION_REGISTER = 5120;

// Power-enable pins are intentionally disabled until the PCB profile is frozen.
// A negative value means the rail is always powered externally.
constexpr int GAS_SENSOR_5V_ENABLE_PIN = -1;
constexpr int PARTICLE_SENSOR_5V_ENABLE_PIN = -1;

}  // namespace iiot
