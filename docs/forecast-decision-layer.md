# Forecast Decision Layer v1

## Tujuan

Forecast decision layer v1 mengubah output angka dari `forecast_payload_v1`
menjadi status lokal yang lebih mudah dibaca:

```text
normal | warning | critical
```

Layer ini masih rule-based. Ini belum risk score, belum prescriptive AI final,
belum MQTT, belum backend/dashboard, dan belum OpenClaw.

## Posisi di Pipeline

```text
LSTM-ready windows
-> predict-lstm-forecast
-> build-forecast-payload-v1
-> build-forecast-decision-v1
-> decision_payloads.jsonl
```

Decision layer berjalan setelah forecast payload terbentuk. Inputnya adalah
prediksi sensor dalam satuan asli, bukan nilai normalized.

## Command

```powershell
py -3.13 run_gateway.py build-forecast-decision-v1 --forecast-payloads models/lstm_forecast/latest/forecast_payloads.jsonl --output models/lstm_forecast/latest/decision_payloads.jsonl
```

Output berada di `models/` dan tidak masuk Git.

## Input

Input adalah JSONL dari `build-forecast-payload-v1`. Field penting:

- `gateway_id`, `node_id`, `room_id`
- `input_start_timestamp`, `input_end_timestamp`
- `forecast_horizon_minutes`
- `predicted_sensor`
- `model_version`, `model_readiness`, `metrics_ref`

`predicted_sensor` dinilai untuk semua target utama:

- `temperature_c`
- `humidity_pct`
- `pressure_hpa`
- `bme_gas_raw`
- `co_raw`

## Output

Contoh output:

```json
{
  "schema": "iiot.ai_sensor.forecast_decision.v1",
  "gateway_id": "gw-test",
  "node_id": "node-1",
  "room_id": "room-a",
  "input_start_timestamp": "2026-06-01T00:00:00+00:00",
  "input_end_timestamp": "2026-06-01T00:12:00+00:00",
  "forecast_horizon_minutes": 5,
  "env_status": "warning",
  "main_factor": "co_raw",
  "reason": "co_raw forecast exceeded warning threshold",
  "predicted_sensor": {
    "temperature_c": 31.2,
    "humidity_pct": 70.5,
    "pressure_hpa": 1012.8,
    "bme_gas_raw": 2300,
    "co_raw": 0.045
  },
  "model_version": "lstm_forecast_v1",
  "model_readiness": "EXPERIMENTAL",
  "metrics_ref": "models/lstm_forecast/latest/metrics.json"
}
```

## Rule v1

Rule mengevaluasi semua sensor target utama:

```text
co_raw >= 0.08               -> critical
co_raw >= 0.04               -> warning

bme_gas_raw >= 3500          -> critical
bme_gas_raw >= 2500          -> warning

temperature_c >= 38          -> critical
temperature_c >= 35          -> warning
temperature_c <= 10          -> warning

humidity_pct >= 90           -> critical
humidity_pct >= 85           -> warning
humidity_pct <= 25           -> warning

pressure_hpa >= 1025         -> warning
pressure_hpa <= 995          -> warning

model_readiness == NOT_READY -> warning
```

Severity dipilih dari rule aktif tertinggi:

```text
critical > warning > normal
```

Jika beberapa rule aktif pada severity yang sama, `main_factor` dipilih dengan
prioritas:

```text
co_raw
bme_gas_raw
temperature_c
humidity_pct
pressure_hpa
model_readiness
```

CO dan gas tetap menjadi prioritas karena paling dekat dengan risiko udara.
Temperature, humidity, dan pressure tetap dipakai agar semua target utama ikut
dinilai.

## Kenapa Belum Risk Score

Risk score butuh bobot risiko yang valid. Pada tahap ini bobot tersebut belum
tervalidasi dengan data sensor real. Rule-based v1 lebih mudah diaudit: setiap
status punya alasan yang eksplisit.

Setelah data real tersedia, rule ini bisa dievaluasi ulang dan dinaikkan menjadi
risk score atau prescriptive decision layer.

## Batasan

- Threshold v1 masih konservatif dan harus divalidasi ulang dengan sensor real.
- `pressure_hpa` pada dataset derived masih synthetic, jadi belum dibuat rule
  critical.
- `bme_gas_raw` pada dataset Gary derived masih proxy, bukan BME688/BME668 real.
- Output ini belum dikirim ke MQTT dan belum dipakai dashboard.
- OpenClaw tetap hanya untuk notifikasi/report/summary setelah backend siap,
  bukan inference realtime.
