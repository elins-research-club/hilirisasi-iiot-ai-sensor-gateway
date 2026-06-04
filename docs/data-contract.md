# Data Contract dan Payload

## Tujuan

Data contract menyambungkan dua layer:

- ESP32-C6 firmware mengirim compact payload melalui LoRa.
- Raspberry Pi Python pipeline mengubah payload menjadi canonical internal, lalu melakukan preprocessing AI/pre-model.

## Compact Payload ESP32-C6

Contoh payload firmware:

```json
{"v":1,"n":"node_01","r":"room_A","ts":42,"seq":7,"st":"ok","q":"valid","f":"","s":{"tc":29.20,"h":65.40,"p":1008.30,"bme":18125.00,"co":0.01200}}
```

Alias field:

- `v`: schema version.
- `n`: node_id.
- `r`: room_id.
- `ts`: timestamp atau uptime seconds. Jika ESP32 belum punya RTC/NTP, Raspberry Pi receiver sebaiknya mengganti/menambah receive_timestamp saat payload diterima.
- `seq`: sequence number.
- `st`: status, contoh `ok` atau `sensor_error`.
- `q`: quality, contoh `valid` atau `invalid`.
- `f`: flags, dipisahkan `|` jika lebih dari satu.
- `s.tc`: temperature_c.
- `s.h`: humidity_pct.
- `s.p`: pressure_hpa.
- `s.bme`: bme_gas_raw.
- `s.co`: co_raw.

## Canonical Internal Raspberry Pi

Raspberry Pi menyamakan payload menjadi field canonical:

- gateway_id atau gw.
- node_id atau n.
- room_id atau r.
- timestamp atau ts.
- sequence atau seq.
- status atau st.
- quality atau q.
- flags atau f.
- sensor.temperature_c.
- sensor.humidity_pct.
- sensor.pressure_hpa.
- sensor.voc_raw.
- sensor.bme_gas_raw.
- sensor.co_raw.
- sensor.gas_raw.

## Mapping Sensor Target

- BME688/BME668: temperature_c, humidity_pct, pressure_hpa, bme_gas_raw atau voc_raw.
- SEN0377: co_raw.

## Mapping Gary Stafford

Gary dipakai sebagai uji awal pipeline, bukan representasi final 1:1.

- temp menjadi temperature_c.
- humidity menjadi humidity_pct.
- co menjadi co_raw atau SEN0377-like CO feature.
- lpg dan smoke menjadi gas proxy awal untuk bme_gas_raw/gas_raw atau pendekatan VOC proxy.
- pressure_hpa unavailable karena Gary tidak punya pressure.
- voc_raw unavailable sebagai VOC/BME688 gas asli; Gary hanya punya gas-like proxy.
- light dan motion menjadi metadata/ignored columns dan tidak masuk fitur utama default.

## Simulasi Gary ke Payload ESP32-C6

Untuk simulasi end-to-end di laptop, Gary diproses dulu oleh simulator
ESP32-like sebelum masuk pipeline Raspberry Pi:

```powershell
py -3.13 run_gateway.py simulate-gary-esp32 --input-csv iot_telemetry_data.csv --output data/simulated/gary_esp32_lora_payloads.jsonl
```

Outputnya memakai compact payload yang sama dengan arah firmware:

```json
{"v":1,"n":"00:0f:00:70:91:0a","r":"gary_public","ts":"2020-07-12T00:01:34.385975+00:00","seq":0,"st":"ok","q":"valid","f":"pressure_unavailable|voc_not_native|gas_proxy_from_lpg_smoke","s":{"tc":25.0,"h":60.0,"bme":0.015,"co":0.01}}
```

Catatan schema:

- `s.bme` berisi gas proxy dari rata-rata LPG/smoke, lalu dibaca sebagai `bme_gas_raw`.
- `s.p` tidak dikirim karena Gary tidak punya pressure.
- `f` memakai string ringkas dipisahkan `|`; parser Raspberry Pi memecahnya menjadi daftar flag.
- `gas_raw` boleh kosong pada payload compact ini karena sinyal gas utama sudah masuk melalui `bme_gas_raw`.
- Simulator ini hanya untuk laptop/testing; firmware real tetap C++ di ESP32-C6.

## Status Evaluasi

Evaluator memisahkan tiga status:

- pipeline_status: PASS/WARN/FAIL untuk keberhasilan preprocessing teknis.
- dataset_coverage_status: FULL/PARTIAL untuk cakupan dataset terhadap sensor target.
- lstm_readiness: READY/READY_WITH_LIMITATIONS/NOT_READY untuk kesiapan window sebagai input LSTM.

Untuk Gary saat ini: pipeline_status PASS, dataset_coverage_status PARTIAL, dan lstm_readiness READY_WITH_LIMITATIONS.

## Output Window LSTM-Ready

File `data/processed/lstm_windows.jsonl` berisi satu sample per baris:

- gateway_id.
- node_id.
- room_id.
- start_timestamp.
- end_timestamp.
- feature_names.
- shape, contoh [12, 16].
- x, matrix [timesteps, features].

Batch model tahap berikutnya membaca JSONL ini menjadi [samples, timesteps, features].

