#include "sensors.h"

#include <math.h>
#include <new>

#include "config.h"
#include "esp_log.h"

#if !IIOT_USE_MOCK_SENSORS
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "uart_bridge.h"

#if IIOT_ENABLE_BOSCH_BME68X
#if __has_include("bme68x.h")
extern "C" {
#include "bme68x.h"
}
#define IIOT_BME68X_DRIVER_AVAILABLE 1
#else
#error "IIOT_ENABLE_BOSCH_BME68X=1 requires the official Bosch BME68x SensorAPI source"
#endif
#else
#define IIOT_BME68X_DRIVER_AVAILABLE 0
#endif
#endif

namespace iiot {
namespace {
constexpr const char* TAG = "sensors";

#if !IIOT_USE_MOCK_SENSORS
constexpr uint8_t SEN0466_PROTOCOL_HEAD = 0xFF;
constexpr uint8_t SEN0466_PROTOCOL_ADDR = 0x01;
constexpr uint8_t SEN0466_CMD_GET_CONCENTRATION = 0x86;
constexpr uint8_t SEN0466_GAS_TYPE_CO = 0x04;
constexpr uint8_t SEN0321_MODE_REGISTER = 0x03;
constexpr uint8_t SEN0321_AUTO_MODE = 0x00;
constexpr uint8_t SEN0321_READ_REGISTER = 0x04;
constexpr uint8_t SEN0321_AUTO_READ = 0x00;
constexpr uint8_t SEN0321_AUTO_DATA_REGISTER = 0x09;
constexpr uint8_t MHZ19_READ_COMMAND = 0x86;
constexpr size_t MHZ19_FRAME_SIZE = 9;
constexpr size_t PMS_FRAME_SIZE = 32;
constexpr uint16_t PMS_FRAME_LENGTH = 28;
constexpr uint8_t PMS_COMMAND_READ = 0xE2;
constexpr uint8_t PMS_COMMAND_MODE = 0xE1;
constexpr uint8_t PMS_COMMAND_SLEEP = 0xE4;

uint8_t twosComplementChecksum(const uint8_t* data, size_t size) {
  uint8_t sum = 0;
  if (data == nullptr || size < 2) {
    return 0;
  }
  for (size_t index = 1; index + 1 < size; ++index) {
    sum = static_cast<uint8_t>(sum + data[index]);
  }
  return static_cast<uint8_t>(~sum + 1U);
}

uint8_t winsenChecksum(const uint8_t* data, size_t size) {
  if (data == nullptr || size != MHZ19_FRAME_SIZE) {
    return 0;
  }
  uint8_t sum = 0;
  for (size_t index = 1; index < size - 1; ++index) {
    sum = static_cast<uint8_t>(sum + data[index]);
  }
  return static_cast<uint8_t>(0xFFU - sum + 1U);
}

void buildPlantowerCommand(uint8_t command, uint8_t data_high, uint8_t data_low,
                           uint8_t (&frame)[7]) {
  frame[0] = 0x42;
  frame[1] = 0x4D;
  frame[2] = command;
  frame[3] = data_high;
  frame[4] = data_low;
  const uint16_t checksum = static_cast<uint16_t>(frame[0] + frame[1] + frame[2] +
                                                   frame[3] + frame[4]);
  frame[5] = static_cast<uint8_t>(checksum >> 8U);
  frame[6] = static_cast<uint8_t>(checksum & 0xFFU);
}

uint16_t readBigEndian16(const uint8_t* data) {
  return static_cast<uint16_t>((static_cast<uint16_t>(data[0]) << 8U) | data[1]);
}

bool validatePlantowerFrame(const uint8_t* frame, size_t size) {
  if (frame == nullptr || size != PMS_FRAME_SIZE || frame[0] != 0x42 || frame[1] != 0x4D ||
      readBigEndian16(frame + 2) != PMS_FRAME_LENGTH) {
    return false;
  }
  uint16_t checksum = 0;
  for (size_t index = 0; index < size - 2; ++index) {
    checksum = static_cast<uint16_t>(checksum + frame[index]);
  }
  return checksum == readBigEndian16(frame + size - 2);
}

bool configurePowerEnable(int pin) {
  if (pin < 0) {
    return true;
  }
  gpio_config_t config{};
  config.pin_bit_mask = 1ULL << static_cast<uint32_t>(pin);
  config.mode = GPIO_MODE_OUTPUT;
  config.pull_up_en = GPIO_PULLUP_DISABLE;
  config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  config.intr_type = GPIO_INTR_DISABLE;
  if (gpio_config(&config) != ESP_OK) {
    return false;
  }
  return gpio_set_level(static_cast<gpio_num_t>(pin), 1) == ESP_OK;
}

#if IIOT_BME68X_DRIVER_AVAILABLE
struct Bme68xContext {
  i2c_master_bus_handle_t bus = nullptr;
  uint8_t address = 0;
};

BME68X_INTF_RET_TYPE bme68xRead(uint8_t reg_addr, uint8_t* reg_data, uint32_t length,
                                void* intf_ptr) {
  if (reg_data == nullptr || length == 0 || intf_ptr == nullptr) {
    return -1;
  }
  auto* context = static_cast<Bme68xContext*>(intf_ptr);
  i2c_device_config_t config{};
  config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  config.device_address = context->address;
  config.scl_speed_hz = I2C_CLOCK_HZ;
  i2c_master_dev_handle_t device = nullptr;
  if (i2c_master_bus_add_device(context->bus, &config, &device) != ESP_OK) {
    return -1;
  }
  const esp_err_t result =
      i2c_master_transmit_receive(device, &reg_addr, 1, reg_data, length, 100);
  i2c_master_bus_rm_device(device);
  return result == ESP_OK ? 0 : -1;
}

BME68X_INTF_RET_TYPE bme68xWrite(uint8_t reg_addr, const uint8_t* reg_data, uint32_t length,
                                 void* intf_ptr) {
  if (reg_data == nullptr || length == 0 || length > 64 || intf_ptr == nullptr) {
    return -1;
  }
  auto* context = static_cast<Bme68xContext*>(intf_ptr);
  i2c_device_config_t config{};
  config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  config.device_address = context->address;
  config.scl_speed_hz = I2C_CLOCK_HZ;
  i2c_master_dev_handle_t device = nullptr;
  if (i2c_master_bus_add_device(context->bus, &config, &device) != ESP_OK) {
    return -1;
  }
  uint8_t payload[65] = {0};
  payload[0] = reg_addr;
  for (uint32_t index = 0; index < length; ++index) {
    payload[index + 1] = reg_data[index];
  }
  const esp_err_t result = i2c_master_transmit(device, payload, length + 1, 100);
  i2c_master_bus_rm_device(device);
  return result == ESP_OK ? 0 : -1;
}

void bme68xDelayUs(uint32_t period, void*) {
  if (period >= 1000) {
    vTaskDelay(pdMS_TO_TICKS((period + 999U) / 1000U));
  } else if (period > 0) {
    esp_rom_delay_us(period);
  }
}
#endif
#endif
}  // namespace

bool SensorReader::begin() {
#if IIOT_USE_MOCK_SENSORS
  ESP_LOGI(TAG, "mock sensor profile enabled");
  return true;
#else
  started_us_ = esp_timer_get_time();
  if (!configurePowerEnable(GAS_SENSOR_5V_ENABLE_PIN) ||
      !configurePowerEnable(PARTICLE_SENSOR_5V_ENABLE_PIN)) {
    ESP_LOGE(TAG, "5 V sensor rail enable failed");
    return false;
  }

  i2c_master_bus_config_t bus_config{};
  bus_config.i2c_port = static_cast<i2c_port_num_t>(I2C_PORT);
  bus_config.sda_io_num = static_cast<gpio_num_t>(I2C_SDA_PIN);
  bus_config.scl_io_num = static_cast<gpio_num_t>(I2C_SCL_PIN);
  bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
  bus_config.glitch_ignore_cnt = 7;
  bus_config.flags.enable_internal_pullup = true;

  i2c_master_bus_handle_t bus = nullptr;
  esp_err_t err = i2c_new_master_bus(&bus_config, &bus);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "i2c_new_master_bus failed: %s", esp_err_to_name(err));
    return false;
  }
  i2c_bus_ = bus;

  const struct {
    const char* name;
    uint8_t address;
  } probes[] = {
      {"BME688", BME688_I2C_ADDRESS},
      {"SEN0466", SEN0466_I2C_ADDRESS},
      {"SEN0321", SEN0321_I2C_ADDRESS},
      {"INA226", INA226_I2C_ADDRESS},
      {"SC16IS752 UART bridge", UART_BRIDGE_I2C_ADDRESS},
  };
  for (const auto& probe : probes) {
    if (probeI2c(probe.address)) {
      ESP_LOGI(TAG, "%s probe ok at 0x%02x", probe.name, probe.address);
    } else {
      ESP_LOGW(TAG, "%s missing at 0x%02x", probe.name, probe.address);
    }
  }

  adc_unit_t unit = ADC_UNIT_1;
  adc_channel_t channel = ADC_CHANNEL_0;
  err = adc_oneshot_io_to_channel(SEN0574_ADC_GPIO, &unit, &channel);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "ADC GPIO mapping failed: %s", esp_err_to_name(err));
    return false;
  }
  adc_oneshot_unit_init_cfg_t unit_config{};
  unit_config.unit_id = unit;
  unit_config.ulp_mode = ADC_ULP_MODE_DISABLE;
  adc_oneshot_unit_handle_t adc_unit = nullptr;
  err = adc_oneshot_new_unit(&unit_config, &adc_unit);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "ADC unit init failed: %s", esp_err_to_name(err));
    return false;
  }
  adc_oneshot_chan_cfg_t channel_config{};
  channel_config.atten = ADC_ATTEN_DB_12;
  channel_config.bitwidth = ADC_BITWIDTH_DEFAULT;
  err = adc_oneshot_config_channel(adc_unit, channel, &channel_config);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "ADC channel config failed: %s", esp_err_to_name(err));
    return false;
  }
  adc_unit_ = adc_unit;
  adc_channel_ = static_cast<int>(channel);

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
  adc_cali_curve_fitting_config_t cali_config{};
  cali_config.unit_id = unit;
  cali_config.chan = channel;
  cali_config.atten = ADC_ATTEN_DB_12;
  cali_config.bitwidth = ADC_BITWIDTH_DEFAULT;
  adc_cali_handle_t cali = nullptr;
  err = adc_cali_create_scheme_curve_fitting(&cali_config, &cali);
  if (err == ESP_OK) {
    adc_cali_ = cali;
  } else {
    ESP_LOGE(TAG, "ADC calibration unavailable: %s", esp_err_to_name(err));
    return false;
  }
#else
  ESP_LOGE(TAG, "ADC curve-fitting calibration is not supported by this target");
  return false;
#endif

  if (probeI2c(SEN0321_I2C_ADDRESS)) {
    const uint8_t mode_command[] = {SEN0321_MODE_REGISTER, SEN0321_AUTO_MODE};
    if (!writeI2c(SEN0321_I2C_ADDRESS, mode_command, sizeof(mode_command))) {
      ESP_LOGW(TAG, "SEN0321 automatic mode write failed");
    }
  }

  if (probeI2c(INA226_I2C_ADDRESS) &&
      !writeI2cRegister16(INA226_I2C_ADDRESS, 0x05, INA226_CALIBRATION_REGISTER)) {
    ESP_LOGW(TAG, "INA226 calibration register write failed");
  }

#if IIOT_BME68X_DRIVER_AVAILABLE
  if (probeI2c(BME688_I2C_ADDRESS)) {
    auto* context = new (std::nothrow) Bme68xContext{};
    auto* device = new (std::nothrow) bme68x_dev{};
    if (context != nullptr && device != nullptr) {
      context->bus = bus;
      context->address = BME688_I2C_ADDRESS;
      device->intf = BME68X_I2C_INTF;
      device->intf_ptr = context;
      device->read = bme68xRead;
      device->write = bme68xWrite;
      device->delay_us = bme68xDelayUs;
      device->amb_temp = 25;
      bme68x_conf conf{};
      conf.filter = BME68X_FILTER_OFF;
      conf.odr = BME68X_ODR_NONE;
      conf.os_hum = BME68X_OS_16X;
      conf.os_pres = BME68X_OS_1X;
      conf.os_temp = BME68X_OS_2X;
      bme68x_heatr_conf heater{};
      heater.enable = BME68X_ENABLE;
      heater.heatr_temp = 300;
      heater.heatr_dur = 100;
      const int8_t init_result = bme68x_init(device);
      const int8_t conf_result = init_result == BME68X_OK ? bme68x_set_conf(&conf, device) : init_result;
      const int8_t heater_result =
          conf_result == BME68X_OK
              ? bme68x_set_heatr_conf(BME68X_FORCED_MODE, &heater, device)
              : conf_result;
      if (heater_result == BME68X_OK) {
        bme68x_dev_ = device;
        ESP_LOGI(TAG, "Bosch BME68x SensorAPI initialized");
      } else {
        ESP_LOGW(TAG, "BME688 SensorAPI init failed: %d", static_cast<int>(heater_result));
        delete device;
        delete context;
      }
    } else {
      delete device;
      delete context;
      ESP_LOGW(TAG, "BME688 SensorAPI allocation failed");
    }
  }
#else
  if (probeI2c(BME688_I2C_ADDRESS)) {
    ESP_LOGW(TAG,
             "BME688 present but Bosch SensorAPI is disabled/unavailable; lane remains invalid");
  }
#endif

  if (probeI2c(UART_BRIDGE_I2C_ADDRESS)) {
    auto* bridge = new (std::nothrow) Sc16is752Bridge{};
    if (bridge != nullptr && bridge->begin(i2c_bus_, UART_BRIDGE_I2C_ADDRESS,
                                           UART_BRIDGE_CRYSTAL_HZ) &&
        bridge->configure(Sc16is752Bridge::Channel::A, MHZ19_BAUD) &&
        bridge->configure(Sc16is752Bridge::Channel::B, PMS7003T_BAUD)) {
      uart_bridge_ = bridge;
      uint8_t wake[7] = {0};
      uint8_t passive[7] = {0};
      buildPlantowerCommand(PMS_COMMAND_SLEEP, 0x00, 0x01, wake);
      buildPlantowerCommand(PMS_COMMAND_MODE, 0x00, 0x00, passive);
      if (!bridge->write(Sc16is752Bridge::Channel::B, wake, sizeof(wake),
                         SENSOR_UART_TIMEOUT_MS) ||
          !bridge->write(Sc16is752Bridge::Channel::B, passive, sizeof(passive),
                         SENSOR_UART_TIMEOUT_MS)) {
        ESP_LOGW(TAG, "PMS7003T wake/passive configuration failed");
      }
      ESP_LOGI(TAG, "SC16IS752 channels initialized for MH-Z19 and PMS7003T");
    } else {
      delete bridge;
      ESP_LOGW(TAG, "SC16IS752 initialization failed; CO2/PM lanes remain invalid");
    }
  }
  return true;
#endif
}

SensorSample SensorReader::read() {
  SensorSample sample{};
#if IIOT_USE_MOCK_SENSORS
  const float phase = static_cast<float>(mock_step_ % 20U) / 20.0F;
  sample.temperature_c = 28.0F + phase;
  sample.humidity_pct = 62.0F + phase * 2.0F;
  sample.pressure_hpa = 1008.0F + phase;
  sample.bme_gas_ohm = 18000.0F + phase * 500.0F;
  sample.co_ppm = 2.0F + phase * 0.2F;
  sample.no2_raw_mv = 420.0F + phase * 10.0F;
  sample.no2_ratio = 1.0F + phase * 0.02F;
  sample.o3_ppm = 0.03F + phase * 0.005F;
  sample.co2_ppm = 650.0F + phase * 20.0F;
  sample.pm1_ug_m3 = 8.0F + phase;
  sample.pm25_ug_m3 = 12.0F + phase * 2.0F;
  sample.pm10_ug_m3 = 18.0F + phase * 3.0F;
  sample.battery_voltage = 4.05F - phase * 0.03F;
  sample.current_ma = 82.0F + phase * 2.0F;
  sample.power_mw = sample.battery_voltage * sample.current_ma;
  sample.health = {true, true, true, true, true, true, true};
  ++mock_step_;
#else
  readBme688(sample);
  readSen0466(sample);
  readSen0574(sample);
  readSen0321(sample);
  readMhz19(sample);
  readPms7003t(sample);
  readIna226(sample);
#endif
  return sample;
}

#if !IIOT_USE_MOCK_SENSORS
bool SensorReader::probeI2c(uint8_t address) const {
  if (i2c_bus_ == nullptr) {
    return false;
  }
  auto bus = static_cast<i2c_master_bus_handle_t>(i2c_bus_);
  return i2c_master_probe(bus, address, 100) == ESP_OK;
}

bool SensorReader::writeI2c(uint8_t address, const uint8_t* data, size_t size) const {
  if (i2c_bus_ == nullptr || data == nullptr || size == 0) {
    return false;
  }
  i2c_device_config_t device_config{};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = address;
  device_config.scl_speed_hz = I2C_CLOCK_HZ;
  i2c_master_dev_handle_t device = nullptr;
  auto bus = static_cast<i2c_master_bus_handle_t>(i2c_bus_);
  esp_err_t err = i2c_master_bus_add_device(bus, &device_config, &device);
  if (err != ESP_OK) {
    return false;
  }
  err = i2c_master_transmit(device, data, size, 100);
  i2c_master_bus_rm_device(device);
  return err == ESP_OK;
}

bool SensorReader::readI2c(uint8_t address, const uint8_t* command, size_t command_size,
                           uint8_t* output, size_t output_size, uint32_t timeout_ms) const {
  if (i2c_bus_ == nullptr || command == nullptr || command_size == 0 || output == nullptr ||
      output_size == 0) {
    return false;
  }
  i2c_device_config_t device_config{};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = address;
  device_config.scl_speed_hz = I2C_CLOCK_HZ;
  i2c_master_dev_handle_t device = nullptr;
  auto bus = static_cast<i2c_master_bus_handle_t>(i2c_bus_);
  esp_err_t err = i2c_master_bus_add_device(bus, &device_config, &device);
  if (err != ESP_OK) {
    return false;
  }
  err = i2c_master_transmit_receive(device, command, command_size, output, output_size, timeout_ms);
  i2c_master_bus_rm_device(device);
  return err == ESP_OK;
}

bool SensorReader::readBme688(SensorSample& sample) const {
#if IIOT_BME68X_DRIVER_AVAILABLE
  if (bme68x_dev_ == nullptr) {
    return false;
  }
  auto* device = static_cast<bme68x_dev*>(bme68x_dev_);
  bme68x_conf conf{};
  conf.filter = BME68X_FILTER_OFF;
  conf.odr = BME68X_ODR_NONE;
  conf.os_hum = BME68X_OS_16X;
  conf.os_pres = BME68X_OS_1X;
  conf.os_temp = BME68X_OS_2X;
  if (bme68x_set_op_mode(BME68X_FORCED_MODE, device) != BME68X_OK) {
    return false;
  }
  const uint32_t delay_us = bme68x_get_meas_dur(BME68X_FORCED_MODE, &conf, device) + 100000U;
  device->delay_us(delay_us, device->intf_ptr);
  bme68x_data data{};
  uint8_t fields = 0;
  if (bme68x_get_data(BME68X_FORCED_MODE, &data, &fields, device) != BME68X_OK || fields == 0 ||
      (data.status & BME68X_NEW_DATA_MSK) == 0 ||
      (data.status & BME68X_GASM_VALID_MSK) == 0 ||
      (data.status & BME68X_HEAT_STAB_MSK) == 0) {
    return false;
  }
#ifdef BME68X_USE_FPU
  sample.temperature_c = data.temperature;
  sample.pressure_hpa = data.pressure / 100.0F;
  sample.humidity_pct = data.humidity;
  sample.bme_gas_ohm = data.gas_resistance;
#else
  sample.temperature_c = static_cast<float>(data.temperature) / 100.0F;
  sample.pressure_hpa = static_cast<float>(data.pressure) / 100.0F;
  sample.humidity_pct = static_cast<float>(data.humidity) / 1000.0F;
  sample.bme_gas_ohm = static_cast<float>(data.gas_resistance);
#endif
  const bool valid = isfinite(sample.temperature_c) && isfinite(sample.pressure_hpa) &&
                     isfinite(sample.humidity_pct) && isfinite(sample.bme_gas_ohm) &&
                     sample.temperature_c >= -40.0F && sample.temperature_c <= 85.0F &&
                     sample.humidity_pct >= 0.0F && sample.humidity_pct <= 100.0F &&
                     sample.pressure_hpa >= 300.0F && sample.pressure_hpa <= 1100.0F &&
                     sample.bme_gas_ohm > 0.0F;
  sample.health.bme688_ok = valid;
  return valid;
#else
  (void)sample;
  return false;
#endif
}

bool SensorReader::readSen0466(SensorSample& sample) const {
  if (!probeI2c(SEN0466_I2C_ADDRESS)) {
    return false;
  }
  uint8_t request[10] = {0};
  request[0] = 0x00;
  request[1] = SEN0466_PROTOCOL_HEAD;
  request[2] = SEN0466_PROTOCOL_ADDR;
  request[3] = SEN0466_CMD_GET_CONCENTRATION;
  request[9] = twosComplementChecksum(request + 1, 9);
  if (!writeI2c(SEN0466_I2C_ADDRESS, request, sizeof(request))) {
    ESP_LOGW(TAG, "SEN0466 request write failed");
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(10));
  const uint8_t register_zero = 0x00;
  uint8_t response[9] = {0};
  if (!readI2c(SEN0466_I2C_ADDRESS, &register_zero, 1, response, sizeof(response))) {
    ESP_LOGW(TAG, "SEN0466 response read failed");
    return false;
  }
  if (response[0] != SEN0466_PROTOCOL_HEAD ||
      response[8] != twosComplementChecksum(response, sizeof(response)) ||
      response[4] != SEN0466_GAS_TYPE_CO || response[5] > 2) {
    ESP_LOGW(TAG, "SEN0466 response validation failed");
    return false;
  }
  float concentration = static_cast<float>(readBigEndian16(response + 2));
  if (response[5] == 1) {
    concentration *= 0.1F;
  } else if (response[5] == 2) {
    concentration *= 0.01F;
  }
  if (!isfinite(concentration) || concentration < 0.0F || concentration > 1000.0F) {
    ESP_LOGW(TAG, "SEN0466 concentration out of contract range");
    return false;
  }
  sample.co_ppm = concentration;
  sample.health.sen0466_ok = true;
  return true;
}

bool SensorReader::readSen0574(SensorSample& sample) const {
  if (adc_unit_ == nullptr || adc_cali_ == nullptr || adc_channel_ < 0) {
    return false;
  }
  int raw = 0;
  auto unit = static_cast<adc_oneshot_unit_handle_t>(adc_unit_);
  esp_err_t err = adc_oneshot_read(unit, static_cast<adc_channel_t>(adc_channel_), &raw);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "SEN0574 ADC read failed: %s", esp_err_to_name(err));
    return false;
  }
  int millivolts = 0;
  auto cali = static_cast<adc_cali_handle_t>(adc_cali_);
  err = adc_cali_raw_to_voltage(cali, raw, &millivolts);
  if (err != ESP_OK || millivolts < 0 || millivolts > 3300) {
    ESP_LOGW(TAG, "SEN0574 ADC calibration/range failed: %s", esp_err_to_name(err));
    return false;
  }
  sample.no2_raw_mv = static_cast<float>(millivolts);
  sample.no2_ratio = NAN;
  sample.health.sen0574_ok = true;
  return true;
}

bool SensorReader::readSen0321(SensorSample& sample) const {
  if (!probeI2c(SEN0321_I2C_ADDRESS)) {
    return false;
  }
  const uint8_t trigger[] = {SEN0321_READ_REGISTER, SEN0321_AUTO_READ};
  if (!writeI2c(SEN0321_I2C_ADDRESS, trigger, sizeof(trigger))) {
    ESP_LOGW(TAG, "SEN0321 trigger write failed");
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(100));
  const uint8_t data_register = SEN0321_AUTO_DATA_REGISTER;
  uint8_t response[2] = {0};
  if (!readI2c(SEN0321_I2C_ADDRESS, &data_register, 1, response, sizeof(response))) {
    ESP_LOGW(TAG, "SEN0321 data read failed");
    return false;
  }
  const float concentration_ppm = static_cast<float>(readBigEndian16(response)) / 1000.0F;
  if (!isfinite(concentration_ppm) || concentration_ppm < 0.0F || concentration_ppm > 10.0F) {
    ESP_LOGW(TAG, "SEN0321 concentration out of contract range");
    return false;
  }
  sample.o3_ppm = concentration_ppm;
  sample.health.sen0321_ok = true;
  return true;
}

bool SensorReader::readMhz19(SensorSample& sample) const {
  if (uart_bridge_ == nullptr) {
    return false;
  }
  const int64_t elapsed_ms = (esp_timer_get_time() - started_us_) / 1000;
  if (elapsed_ms < static_cast<int64_t>(MHZ19_WARMUP_MS)) {
    sample.mhz19_warming = true;
    return false;
  }
  uint8_t request[MHZ19_FRAME_SIZE] = {0xFF, 0x01, MHZ19_READ_COMMAND, 0, 0, 0, 0, 0, 0};
  request[8] = winsenChecksum(request, sizeof(request));
  if (!uart_bridge_->flushRx(Sc16is752Bridge::Channel::A) ||
      !uart_bridge_->write(Sc16is752Bridge::Channel::A, request, sizeof(request),
                           SENSOR_UART_TIMEOUT_MS)) {
    return false;
  }
  uint8_t response[MHZ19_FRAME_SIZE] = {0};
  if (uart_bridge_->read(Sc16is752Bridge::Channel::A, response, sizeof(response),
                         SENSOR_UART_TIMEOUT_MS) != sizeof(response) ||
      response[0] != 0xFF || response[1] != MHZ19_READ_COMMAND ||
      response[8] != winsenChecksum(response, sizeof(response))) {
    ESP_LOGW(TAG, "MH-Z19 response validation failed");
    return false;
  }
  const float concentration = static_cast<float>(readBigEndian16(response + 2));
  if (!isfinite(concentration) || concentration < 0.0F || concentration > 10000.0F) {
    ESP_LOGW(TAG, "MH-Z19 concentration out of contract range");
    return false;
  }
  sample.co2_ppm = concentration;
  sample.health.mhz19_ok = true;
  return true;
}

bool SensorReader::readPms7003t(SensorSample& sample) const {
  if (uart_bridge_ == nullptr) {
    return false;
  }
  const int64_t elapsed_ms = (esp_timer_get_time() - started_us_) / 1000;
  if (elapsed_ms < static_cast<int64_t>(PMS7003T_WARMUP_MS)) {
    sample.pms7003t_warming = true;
    return false;
  }
  uint8_t request[7] = {0};
  buildPlantowerCommand(PMS_COMMAND_READ, 0x00, 0x00, request);
  if (!uart_bridge_->flushRx(Sc16is752Bridge::Channel::B) ||
      !uart_bridge_->write(Sc16is752Bridge::Channel::B, request, sizeof(request),
                           SENSOR_UART_TIMEOUT_MS)) {
    return false;
  }
  uint8_t response[PMS_FRAME_SIZE] = {0};
  if (uart_bridge_->read(Sc16is752Bridge::Channel::B, response, sizeof(response),
                         SENSOR_UART_TIMEOUT_MS) != sizeof(response) ||
      !validatePlantowerFrame(response, sizeof(response))) {
    ESP_LOGW(TAG, "PMS7003T frame validation failed");
    return false;
  }
  sample.pm1_ug_m3 = static_cast<float>(readBigEndian16(response + 10));
  sample.pm25_ug_m3 = static_cast<float>(readBigEndian16(response + 12));
  sample.pm10_ug_m3 = static_cast<float>(readBigEndian16(response + 14));
  const bool valid = sample.pm1_ug_m3 <= 5000.0F && sample.pm25_ug_m3 <= 5000.0F &&
                     sample.pm10_ug_m3 <= 5000.0F;
  sample.health.pms7003t_ok = valid;
  return valid;
}

bool SensorReader::readI2cRegister16(uint8_t address, uint8_t reg, uint16_t& value) const {
  if (i2c_bus_ == nullptr) {
    return false;
  }
  i2c_device_config_t device_config{};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = address;
  device_config.scl_speed_hz = I2C_CLOCK_HZ;
  i2c_master_dev_handle_t device = nullptr;
  auto bus = static_cast<i2c_master_bus_handle_t>(i2c_bus_);
  esp_err_t err = i2c_master_bus_add_device(bus, &device_config, &device);
  if (err != ESP_OK) {
    return false;
  }
  uint8_t bytes[2] = {0, 0};
  err = i2c_master_transmit_receive(device, &reg, 1, bytes, sizeof(bytes), 100);
  i2c_master_bus_rm_device(device);
  if (err != ESP_OK) {
    return false;
  }
  value = readBigEndian16(bytes);
  return true;
}

bool SensorReader::writeI2cRegister16(uint8_t address, uint8_t reg, uint16_t value) const {
  if (i2c_bus_ == nullptr) {
    return false;
  }
  i2c_device_config_t device_config{};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = address;
  device_config.scl_speed_hz = I2C_CLOCK_HZ;
  i2c_master_dev_handle_t device = nullptr;
  auto bus = static_cast<i2c_master_bus_handle_t>(i2c_bus_);
  esp_err_t err = i2c_master_bus_add_device(bus, &device_config, &device);
  if (err != ESP_OK) {
    return false;
  }
  const uint8_t bytes[] = {
      reg,
      static_cast<uint8_t>((value >> 8U) & 0xFFU),
      static_cast<uint8_t>(value & 0xFFU),
  };
  err = i2c_master_transmit(device, bytes, sizeof(bytes), 100);
  i2c_master_bus_rm_device(device);
  return err == ESP_OK;
}

bool SensorReader::readIna226(SensorSample& sample) const {
  if (!probeI2c(INA226_I2C_ADDRESS)) {
    return false;
  }
  uint16_t bus_raw = 0;
  uint16_t current_raw_unsigned = 0;
  uint16_t power_raw = 0;
  if (!readI2cRegister16(INA226_I2C_ADDRESS, 0x02, bus_raw) ||
      !readI2cRegister16(INA226_I2C_ADDRESS, 0x04, current_raw_unsigned) ||
      !readI2cRegister16(INA226_I2C_ADDRESS, 0x03, power_raw)) {
    ESP_LOGW(TAG, "INA226 register read failed");
    return false;
  }
  const int16_t current_raw = static_cast<int16_t>(current_raw_unsigned);
  sample.battery_voltage = static_cast<float>(bus_raw) * 0.00125F;
  sample.current_ma = static_cast<float>(current_raw) * INA226_CURRENT_LSB_MA;
  sample.power_mw = static_cast<float>(power_raw) * 25.0F * INA226_CURRENT_LSB_MA;
  const bool valid = isfinite(sample.battery_voltage) && isfinite(sample.current_ma) &&
                     isfinite(sample.power_mw);
  sample.health.ina226_ok = valid;
  return valid;
}
#endif

}  // namespace iiot
