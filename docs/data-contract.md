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
- sensor.bme_gas_raw.
- sensor.co_raw.
- sensor.gas_raw.

## Mapping Sensor Target

- BME688/BME668: temperature_c, humidity_pct, pressure_hpa, bme_gas_raw/gas resistance style signal.
- SEN0377: co_raw.

Catatan: VOC/IAQ bukan field mentah utama. Jika nanti memakai BSEC atau model tambahan, VOC/IAQ sebaiknya menjadi output olahan seperti `iaq_score` atau `gas_risk_score`, bukan pengganti `bme_gas_raw`.

## Mapping Gary Stafford

Gary dipakai sebagai uji awal pipeline, bukan representasi final 1:1.

- temp menjadi temperature_c.
- humidity menjadi humidity_pct.
- co menjadi co_raw atau SEN0377-like CO feature.
- lpg dan smoke menjadi gas proxy awal untuk bme_gas_raw.
- pressure_hpa unavailable karena Gary tidak punya pressure.
- light dan motion menjadi metadata/ignored columns dan tidak masuk fitur utama default.

## Dataset Turunan Gary Project Schema

Workflow terbaru membuat dataset baru yang kolomnya disesuaikan dengan sensor project, tanpa mengubah CSV Gary asli:

```powershell
py -3.13 run_gateway.py derive-gary-schema --input-csv iot_telemetry_data.csv --output-csv data/derived/gary_project_sensor_schema.csv --output-jsonl data/derived/gary_project_sensor_schema.jsonl --output-payloads data/derived/gary_project_sensor_payloads.jsonl
```

CSV turunan:

```text
timestamp,node_id,sequence,temperature_c,humidity_pct,pressure_hpa,bme_gas_raw,co_raw
```

Aturan derivasi:

- `pressure_hpa` dibuat synthetic karena Gary tidak punya pressure asli. Mode
  default `smooth` mempertahankan tren barometrik halus sekitar 1008-1014 hPa.
  Untuk eksperimen model, mode `dynamic` dapat dipakai agar pressure punya
  variasi cuaca/indoor/noise deterministik yang lebih realistis dan tidak
  terlalu menguntungkan baseline last-value.
- `bme_gas_raw` dibuat dari kombinasi LPG/smoke Gary, lalu diskalakan ke rentang 500-4500 agar cocok dengan normalisasi pipeline.
- `light` dan `motion` dibuang dari schema utama.
- Data turunan ini untuk belajar, simulasi pipeline, dan validasi bentuk data. Data real sensor tetap menjadi sumber kebenaran final.

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

Untuk workflow `derive-gary-schema`: pipeline_status PASS, dataset_coverage_status FULL, dan lstm_readiness READY. Untuk workflow Gary lama tanpa pressure turunan, status coverage tetap PARTIAL.

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
