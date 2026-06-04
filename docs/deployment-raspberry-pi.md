# Deployment Raspberry Pi

## Paket Sistem

`ash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
`

## Setup Project

`ash
git clone <repo-url> iiot-ai-sensor-gateway
cd iiot-ai-sensor-gateway
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .[dev]
cp .env.example .env
python run_gateway.py check-config
`

## Manual Run

`ash
python run_gateway.py simulate --scenario mixed --count 120 --nodes 2
python run_gateway.py run --input-file data/simulated/payloads.jsonl --output-dir data/processed
`

## Systemd

Salin systemd/iiot-ai-sensor-gateway.service ke /etc/systemd/system/, sesuaikan WorkingDirectory, User, dan path virtualenv.

`ash
sudo systemctl daemon-reload
sudo systemctl enable iiot-ai-sensor-gateway
sudo systemctl start iiot-ai-sensor-gateway
sudo journalctl -u iiot-ai-sensor-gateway -f
`

## Catatan Performa

- Interval inference/model berikutnya tidak perlu realtime tinggi; window 1 menit x 12 timestep cukup untuk awal.
- Simpan log JSONL dengan rotasi di deployment produksi.
- Pisahkan proses receiver LoRa dari pipeline jika serial read perlu reconnect khusus.
