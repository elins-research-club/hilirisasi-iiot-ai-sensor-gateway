# Deployment AI Sensor Gateway pada `iiotgw`

Dokumen ini adalah runbook target untuk runtime **baru** berbasis ChirpStack.
Jalur serial/E32 lama di `docs/deployment-raspberry-pi.md` tetap dipertahankan
dan tidak dihapus.

## Boundary

```text
existing:
ChirpStack -> Node1/Node2 forwarder -> central EMQX -> Telegraf -> Prometheus

new parallel lane:
ChirpStack -> AI Sensor Gateway -> raw/canonical -> anomaly/forecast shadow
           -> sensor_ai.v2 -> central EMQX (hanya setelah publish gate)
```

Runtime baru tidak mengedit `lora_pkt_fwd`, konfigurasi radio, protected
`chirpstack_to_emqx.py`, atau existing per-node forwarder.

## Release Layout

```text
~/apps/iiot-ai-sensor-gateway/
├── releases/<git-sha>/
└── current -> releases/<git-sha>

~/.local/state/iiot-ai-sensor-gateway/
~/.config/iiot-ai-sensor-gateway/runtime.env
~/.config/systemd/user/iiot-ai-sensor-gateway.service
```

Release berasal dari `git archive`, bukan rsync workspace mentah. Dataset,
model eksperimen, `.pio`, cache, dan credential tidak ikut. Hanya model CO2
yang dipilih untuk shadow test yang ditambahkan ke bundle setelah SHA-256
diverifikasi terhadap manifest.

## Build + Deploy

Dari VPS canonical setelah source sudah committed:

```bash
cd /home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway
bash deployment/iiotgw/deploy-via-tailscale.sh
```

Script:

1. menolak tracked dirty worktree;
2. membuat versioned release dari Git HEAD;
3. menambahkan selected CO2 model saja;
4. memverifikasi hash model;
5. upload ke release directory baru;
6. membuat venv terpisah;
7. install `.[mqtt,ml]`;
8. menjalankan compile + full unit tests + config check di Pi;
9. baru mengubah symlink `current`.

## Install Service — Masih Inactive

Di target:

```bash
cd ~/apps/iiot-ai-sensor-gateway/current
bash deployment/iiotgw/install-user-service.sh
bash deployment/iiotgw/verify-target.sh
```

Initial env:

```text
IIOT_LIVE_MODE=shadow_ingest
IIOT_PUBLISH_ENABLED=false
IIOT_FORECAST_ENABLED=false
```

Jalankan manual bounded terlebih dahulu:

```bash
.venv/bin/python run_gateway.py run-live-chirpstack \
  --config config/iiotgw.toml --max-messages 4
```

Periksa `raw_events.jsonl`, `accepted_events.jsonl`, dan exact DevEUI/fCnt.

## Promotion

Urutan wajib:

```text
shadow_ingest
-> shadow_ai
-> shadow_ai + forecast
-> publish_ai
```

Edit hanya `~/.config/iiot-ai-sensor-gateway/runtime.env`, kemudian:

```bash
systemctl --user restart iiot-ai-sensor-gateway.service
```

Untuk publish:

```text
IIOT_LIVE_MODE=publish_ai
IIOT_PUBLISH_ENABLED=true
```

Untuk forecast shadow:

```text
IIOT_FORECAST_ENABLED=true
```

Forecast memakai manifest
`deployment/model-manifests/co2_fits_pi5_20260828.json`, memverifikasi hash,
window 16, cadence 60 s, dan target CO2. Cadence/feature/hash mismatch membuat
runtime abstain; model tidak boleh dipaksa.

## Service Lifecycle

Setelah manual gate lulus:

```bash
systemctl --user start iiot-ai-sensor-gateway.service
systemctl --user status iiot-ai-sensor-gateway.service --no-pager
```

Enable hanya setelah shadow observation stabil:

```bash
systemctl --user enable iiot-ai-sensor-gateway.service
```

User `iiotgw` sudah memiliki `Linger=yes`, sehingga enabled user service dapat
dihidupkan user manager saat boot tanpa login interaktif.

## Rollback

AI lane dapat dihentikan tanpa memutus current telemetry:

```bash
systemctl --user disable --now iiot-ai-sensor-gateway.service
```

Untuk rollback release, arahkan `current` ke release sebelumnya lalu ulang
preflight/replay sebelum start.

Raw state/outbox tidak dihapus saat rollback.

## Acceptance

Deployment baru hanya dinyatakan E2E bila:

- Node 1 dan Node 2 exact fCnt terlihat pada raw+accepted AI log;
- current `verify_live_path.sh` tetap PASS;
- target full test suite PASS;
- service tidak restart-loop;
- model hanya infer setelah manifest/cadence/window gate;
- publish mode mencapai central EMQX topic `iot/iiotgw/data`;
- retained `iot/iiotgw/status/sensor` tersedia;
- outbox kembali 0 setelah broker confirmation;
- resource/thermal/log growth terukur;
- rollback tetap tersedia.
