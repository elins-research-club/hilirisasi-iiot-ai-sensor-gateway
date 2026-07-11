# Deployment Raspberry Pi — Sensor Gateway

Dokumen ini adalah contoh deployment, bukan bukti production readiness.

## Prasyarat

- Raspberry Pi OS 64-bit;
- Python environment proyek;
- serial device/group permission;
- config explicit, termasuk `[preprocessing]` version/filter policy;
- writable append-only state directory;
- gateway clock/UTC policy;
- E32/receiver hardware bila live mode;
- firmware node v3 atau explicit v2 compatibility registry.

Dependency commands hanya dijalankan user setelah review:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -e '.[serial]'
```

ML optional:

```bash
.venv/bin/python -m pip install -e '.[ml]'
.venv/bin/python -m pip install -e '.[streaming]'
```

## Preflight

```bash
.venv/bin/python run_gateway.py check-config --config config/default.toml
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
```

Serial discovery/permission harus diverifikasi terhadap device aktual. Jangan hardcode `/dev/ttyUSB0` tanpa pemeriksaan.

## Manual Live Run

```bash
IIOT_CONFIG_FILE=/home/pi/iiot-ai-sensor-gateway/config/default.toml \
.venv/bin/python run_gateway.py receive-real-live \
  --port /dev/ttyUSB0 \
  --baudrate 9600 \
  --output-dir /var/lib/iiot-ai-sensor-gateway/real_live_logs
```

Receiver menulis append-only audit trail. Gateway menerima v3 hardware observation dan v2 compatibility; v2 tidak boleh difilter ulang secara default. Sebelum service live, replay satu golden v3/warm-up/error frame dan periksa provenance/version.

## Systemd Example

Unit: `systemd/iiot-ai-sensor-gateway.service`.

Karakteristik:

- live serial command;
- `Restart=on-failure`;
- restart delay dan rate limit;
- config/env explicit;
- state path writable;
- filesystem hardening dasar.

Sebelum install:

1. sesuaikan user/group/path;
2. buat state directory dengan owner service;
3. set serial group/udev;
4. periksa environment file;
5. run manual dahulu;
6. baru copy/enable unit.

Contoh command deployment tidak dijalankan otomatis oleh agent:

```bash
sudo install -d -o pi -g pi -m 0750 /var/lib/iiot-ai-sensor-gateway
sudo install -m 0644 systemd/iiot-ai-sensor-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iiot-ai-sensor-gateway.service
```

## MQTT

Repo saat ini belum menjalankan production publisher. Sebelum menambah MQTT service, wajib ada:

- TLS/credential source yang aman;
- QoS/retain policy;
- LWT status sensor;
- event spool/outbox bounded;
- status tidak masuk outbox;
- topic ACL;
- schema validation;
- dedupe event ID;
- reconnect/soak test.

## Model Deployment

Model tidak menjadi service default. Promotion memerlukan:

- validation-selected applicable baseline gate;
- cadence/horizon, active feature schema/hash, target degeneracy, clipping/saturation, dan target-coverage gate;
- repeated-seed real-data evaluation;
- safe checkpoint;
- versioned artifact;
- Pi latency/RSS measurement;
- fallback rules/baseline;
- rollback.

## Rollback

- stop/disable unit baru;
- restore previous unit/config backup;
- preserve append-only data directory;
- jangan hapus raw capture atau model artifact tanpa backup;
- verify manual replay before restarting live;
- gateway rollback tetap membaca v2 selama migration window;
- jangan menghapus L0 raw atau v2 parser pada hari cutover node terakhir.

## Belum Diverifikasi

- exact Pi model/OS;
- serial device path;
- E32 transport;
- production MQTT;
- sensor hardware;
- Pi resource metrics;
- long soak/power-loss recovery.
