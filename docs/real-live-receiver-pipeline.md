# Real-Live Receiver Pipeline

Receiver membaca newline-delimited JSON dari replay file atau serial transport, lalu menyimpan jejak raw dan hasil canonical tanpa menimpa data run sebelumnya.

## Alur

```text
replay file / Ebyte serial
-> raw line
-> JSON envelope decode
-> compact payload extract
-> parser v2
-> validation
-> accepted atau rejected
-> append-only audit logs
```

## Command

Replay:

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py receive-real-live \
  --replay-file tests/fixtures/real_payload_samples.jsonl \
  --output-dir data/real_live_logs \
  --max-messages 8
```

Serial:

```bash
python -m pip install -e '.[serial]'
$PY run_gateway.py receive-real-live \
  --port /dev/ttyUSB0 \
  --baudrate 9600 \
  --timeout 1.0 \
  --output-dir data/real_live_logs
```

Optional controls:

```text
--rotate-max-bytes
--idle-sleep-sec
--reconnect-initial-sec
--reconnect-max-sec
```

`max-messages=0` berarti live terus sampai dihentikan.

## Output

| File | Isi |
|---|---|
| `raw_envelopes.jsonl` | raw line, receive time, source/radio, parse status |
| `accepted_payloads.jsonl` | payload asli, canonical record, event ID, issues soft |
| `rejected_payloads.jsonl` | raw line, kategori error, alasan, issues validation |
| `receiver_events.jsonl` | start/stop summary tiap run |

Semua file dibuka dalam mode append. Saat file melebihi batas, file lama diubah nama secara atomik lalu receiver membuat file append baru. Restart receiver tidak menghapus accepted/rejected/events/raw sebelumnya.

## Envelope yang Didukung

Bare compact payload:

```json
{"v":2,"n":"node-1","r":"room-A","ts":77,"seq":1,"bid":"boot-a","st":"ok","q":"valid","f":[],"ok":{"bme688":"ok"},"s":{"tc":28,"h":60}}
```

Wrapped payload dari radio receiver:

```json
{
  "payload": {"v":2,"n":"node-1","r":"room-A","ts":77,"seq":1,"bid":"boot-a","st":"ok","q":"valid","f":[],"ok":{"bme688":"ok"},"s":{"tc":28,"h":60}},
  "radio": {"rssi":-91,"snr":7.1}
}
```

`payload` juga boleh berupa JSON object string. Metadata radio harus object.

## Kategori Reject

- `empty_line`;
- `json_decode_error`;
- `parse_error`;
- `validation_error`.

Frame invalid tidak menghasilkan canonical sensor event.

## Serial Reliability

`SerialLineSource` memiliki:

- blocking timeout;
- explicit sleep ketika `readline()` kosong;
- reconnect setelah exception transport;
- exponential backoff sampai batas maksimum;
- close connection pada reconnect/shutdown generator.

Ini mencegah polling kosong menggunakan 100% CPU. Test menggunakan fake serial source membuktikan sleep dan backoff dipanggil; angka CPU host/Pi belum diukur dan tidak diklaim.

## Time dan Identity

Receiver menambahkan `receive_timestamp`. Uptime node tetap disimpan sebagai `node_timestamp`; waktu event memakai receive time dengan `time_quality=gateway_received`.

Stable event ID membuat replay/retry frame yang sama dapat dideduplikasi. Boot baru membentuk identity cycle baru.

## Systemd

Unit contoh `systemd/iiot-ai-sensor-gateway.service` menjalankan live serial mode dan memakai:

- `Restart=on-failure`;
- `RestartSec=5`;
- start-rate limit;
- writable state path khusus;
- environment file opsional.

Unit tidak lagi menjalankan batch input dengan `Restart=always`.

## Yang Belum Diverifikasi

- nama device serial pada Raspberry Pi target;
- konfigurasi radio E32 fisik;
- framing tambahan dari receiver radio aktual;
- signal metadata RSSI/SNR dari hardware;
- permission/group serial;
- soak test reconnect/power-loss multi-hari;
- local spool untuk MQTT publish.
