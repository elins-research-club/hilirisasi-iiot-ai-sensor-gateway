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

### Cold-boot compatibility untuk dua node USB

Acceptance reboot 25 September 2026 menemukan dua ESP/LoRa node current
terhubung melalui CH9102 USB-UART dan ikut power-reset saat Raspberry Pi
reboot. WM1302, Gateway Bridge, dan packet-forwarder kembali hidup, tetapi
kedua node tidak kembali mengirim uplink sampai serial bridge dibuka dengan
DTR/RTS deasserted.

Evidence current:

- stable USB IDs `5926016290` dan `58EF071105`;
- keduanya menampilkan ESP boot banner pada 115200 baud;
- log menunjukkan `[LoRaWAN] Joining LoRaWAN Network...` lalu
  `Join Success!`;
- sesudah release control-line, kedua DevEUI kembali terlihat di ChirpStack,
  OTAA mendapat DevAddr baru, fCnt restart, dan legacy Prometheus path kembali
  PASS.

Karena itu deployment memasang user unit terpisah
`iiot-node-usb-release.service`. Unit tersebut:

- berjalan sekali setelah `lora-packet-forwarder.service`;
- menunggu 8 detik supaya concentrator siap;
- membuka **hanya** dua stable `/dev/serial/by-id` current di 115200 baud;
- menetapkan DTR/RTS false dan tidak menulis byte apa pun ke node;
- menjalankan dua bounded release pass dengan jeda 5 detik; cold-boot evidence
  menunjukkan pass pertama dapat berhenti di `POWERON_RESET` pada salah satu
  board, sedangkan release kedua mengembalikan kedua DevEUI;
- tidak flash firmware, tidak menyentuh GPIO/WM1302, dan tidak restart service
  HardProg;
- memakai `RemainAfterExit=yes`, sehingga restart AI service biasa tidak
  mengulang recovery node.

Ini compatibility/boot-sequencing shim untuk hardware current. Firmware lama,
serial/E32 path, dan ownership HardProg tetap dipertahankan.

## Release Layout

```text
~/apps/iiot-ai-sensor-gateway/
├── releases/<git-sha>/
└── current -> releases/<git-sha>

~/.local/state/iiot-ai-sensor-gateway/
~/.config/iiot-ai-sensor-gateway/runtime.env
~/.config/systemd/user/iiot-ai-sensor-gateway.service
~/.config/systemd/user/iiot-node-usb-release.service
```

Release berasal dari `git archive`, bukan rsync workspace mentah. Dataset,
model eksperimen, `.pio`, cache, dan credential tidak ikut. Runtime hanya
membawa artifact FITS NumPy kecil yang diekspor dari checkpoint CO2 terverifikasi
dan SHA-256-nya dikunci di manifest. Source checkpoint PyTorch tetap disimpan
sebagai evidence/training artifact di workspace, tetapi tidak dipasang ke gateway.

## Build + Deploy

Dari VPS canonical setelah source sudah committed:

```bash
cd /home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway
bash deployment/iiotgw/deploy-via-tailscale.sh
```

Script:

1. menolak tracked dirty worktree;
2. membuat versioned release dari Git HEAD;
3. memverifikasi selected CO2 NumPy runtime artifact;
4. mempertahankan provenance hash source checkpoint;
5. upload ke release directory baru;
6. membuat venv terpisah;
7. install runtime + target-QA extras `.[mqtt,edge,serial,dev]` secara
   non-editable (Paho + NumPy + PySerial + pytest/ruff; tanpa PyTorch/CUDA);
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

Pada `sensor_ai.v2`, hasil forecast shadow ikut dibawa di `ai.forecast`
beserta nilai prediksi, horizon, model/runtime provenance, readiness, dan
latency. Status yang diharapkan:

```text
waiting_for_resample -> warming -> available_shadow
```

`not_target_node` sah untuk node yang tidak mempunyai artifact model.

### Restart state

Runtime membaca kembali bagian akhir `accepted_events.jsonl` saat startup untuk
merehidrasi state anomaly dan window forecast secara bounded. Tujuannya agar
restart service / restart user-manager tidak menghapus seluruh warm-up evidence.
Rehydration:

- tidak mem-publish ulang event lama;
- tidak memasukkan ulang event ke canonical pipeline;
- hanya memakai `chirpstack_live.v1` accepted observations;
- dibatasi maksimum 4096 canonical records;
- anomaly hanya memakai maksimum jumlah sample warm-up per node;
- forecast direkonstruksi dari bucket 60 detik target model.

Semua state forecast, termasuk `warming` atau abstain, membawa
`model_manifest_id` dan model/runtime provenance.

Jika restart terjadi di tengah bucket menit yang sama dengan bucket terakhir
hasil rehydrasi, update dengan timestamp bucket yang sama diperlakukan
idempotent (replace last bucket), bukan sebagai cadence mismatch. Bucket yang
benar-benar loncat/terlambat tetap fail-closed.

Runtime inference menggunakan NumPy rFFT + linear head yang ekuivalen dengan
FITS edge checkpoint. Parity test pada host membandingkan export terhadap
checkpoint PyTorch asli; ini sengaja menghindari instalasi ratusan MB PyTorch
serta CUDA toolkit yang tidak relevan pada Raspberry Pi CPU.

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
