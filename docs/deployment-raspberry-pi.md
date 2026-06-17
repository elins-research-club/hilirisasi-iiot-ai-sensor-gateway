# Deployment Raspberry Pi

## Paket Sistem

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

## Setup Project

```bash
git clone <repo-url> iiot-ai-sensor-gateway
cd iiot-ai-sensor-gateway
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .[dev]
cp .env.example .env
python run_gateway.py check-config
```

Untuk serial LoRa/UART, install dependency opsional:

```bash
python -m pip install -e ".[serial]"
```

## Manual Run

Simulasi dan preprocessing lokal:

```bash
python run_gateway.py simulate --scenario mixed --count 120 --nodes 2
python run_gateway.py run --input-file data/simulated/payloads.jsonl --output-dir data/processed
```

Replay receiver live tanpa hardware:

```bash
python run_gateway.py receive-real-live --replay-file tests/fixtures/real_payload_samples.jsonl --output-dir data/real_live_logs --max-messages 10
```

Receiver serial skeleton untuk LoRa transparent UART:

```bash
python run_gateway.py receive-real-live --port /dev/serial0 --baudrate 9600 --timeout 1.0 --output-dir data/real_live_logs
```

## Systemd

Salin `systemd/iiot-ai-sensor-gateway.service` ke `/etc/systemd/system/`, lalu
sesuaikan `WorkingDirectory`, `User`, dan path virtualenv.

```bash
sudo systemctl daemon-reload
sudo systemctl enable iiot-ai-sensor-gateway
sudo systemctl start iiot-ai-sensor-gateway
sudo journalctl -u iiot-ai-sensor-gateway -f
```

## Catatan Performa

- Interval inference/model berikutnya tidak perlu realtime tinggi; window 1
  menit x 12 timestep cukup untuk awal.
- Simpan log JSONL dengan rotasi di deployment produksi.
- Receiver v0 hanya skeleton logging/validasi; konfigurasi Ebyte E32/E22, pin,
  channel, dan reconnect kompleks perlu disesuaikan saat hardware real siap.
