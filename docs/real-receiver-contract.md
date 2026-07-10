# Kontrak Real Receiver

## Input

Satu JSON object per baris, berupa:

- compact v2 langsung; atau
- wrapper dengan `payload` dan optional `radio`.

Compact v2 wajib memiliki:

```text
v=2,n,r,ts,seq,bid,st,q,f,ok,s
```

`gw` optional bila gateway default disediakan config.

## Time

Receiver menetapkan waktu terima UTC. Uptime node tetap disimpan sebagai provenance dan tidak dianggap waktu kalender.

## Accepted Record

Accepted JSONL menyimpan:

- raw line;
- payload original;
- canonical record;
- event ID;
- node/room/boot/sequence;
- time quality;
- validation soft issues;
- source/radio metadata.

## Rejected Record

Rejected JSONL menyimpan:

- raw line;
- receive time;
- source metadata;
- category;
- error;
- validation issues bila tersedia.

Kategori: empty, JSON decode, parse, atau validation.

## Ordering

- duplicate sequence dalam boot sama ditolak;
- sequence lebih kecil ditolak;
- event ID duplicate ditolak;
- sequence gap diterima dan dicatat;
- boot ID berubah diterima sebagai reboot.

## Partial Data

Payload partial dapat diterima bila:

- minimal satu sensor valid;
- status/quality sesuai;
- value yang ada finite dan dalam sanity range;
- missing menjadi null/status, bukan angka palsu.

## Persistence

Raw/accepted/rejected/events append-only. Rotation memakai atomic rename. Receiver restart tidak truncate file lama.

## Serial

Serial source wajib memiliki timeout, idle sleep, reconnect, dan backoff. Port tidak tersedia atau disconnect tidak boleh menghasilkan tight loop.

## Batas Transport

Receiver belum mengonfigurasi radio E32 over-air parameter. Ia mengonsumsi newline JSON dari transparent serial/receiver bridge. Konfigurasi channel/address/air-rate/CRC radio fisik adalah hardware integration task.
