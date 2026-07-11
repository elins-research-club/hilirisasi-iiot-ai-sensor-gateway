# Kontrak Real Receiver

## Input

Satu JSON object per baris:

- compact v3 hardware observation;
- compact v2 legacy node-preprocessed observation;
- v1 migration bila config mengizinkan;
- atau wrapper `payload` + optional `radio`.

V3 wajib:

```text
v=3,n,r,ts,tb,seq,bid,pp,fw,cfg,cal,hs,f,ok,s
pp=hardware_only
```

V2 semantics tidak berubah dan tidak difilter ulang secara default.

## Time

Receiver menetapkan UTC receive time. `tb` dan node timestamp dipertahankan. Uptime tidak dianggap kalender/epoch.

## Accepted Record

Accepted JSONL menyimpan:

- raw line/payload asli;
- canonical record;
- event ID;
- node/room/boot/sequence;
- node/receive timestamp, time basis, time quality;
- processing profile;
- firmware/config/calibration/preprocessing version;
- hardware summary/sensor status/flags;
- validation soft issues;
- source/radio metadata.

## Rejected Record

Rejected JSONL menyimpan raw line, receive time, source, category, error, dan validation issues bila tersedia. Secret tidak boleh ada di payload/log.

Kategori utama:

```text
empty
json_decode
parse/schema/profile
validation
ordering/duplicate
```

## Ordering

- duplicate sequence/event ID pada boot sama ditolak;
- sequence lebih kecil ditolak;
- gap diterima dengan issue;
- boot ID baru dianggap reboot;
- gateway semantic filter state diisolasi per boot;
- late/out-of-order tidak disamakan dengan missing.

## Partial Hardware Observation

Dapat diterima bila:

- minimal satu sensor valid;
- hardware summary/status sesuai;
- nilai finite dan valid;
- missing/warming/timeout menjadi null + state/flag;
- NO₂ tidak difabrikasi menjadi ppm.

Final semantic quality tetap ditentukan gateway.

## Persistence

```text
raw_envelopes.jsonl
accepted_payloads.jsonl
rejected_payloads.jsonl
receiver_events.jsonl
```

Append-only, atomic rotation, restart tidak truncate. L0 raw menjadi source of truth untuk replay/reprocessing.

## Serial

Serial source wajib timeout, idle sleep, reconnect, dan exponential backoff. Port kosong/disconnect tidak boleh tight loop.

## Downstream Layering

Receiver memberi L0/L1 ke pipeline. Replay pipeline menghasilkan L2/L3/L4 dan menyimpan `source_event_ids` + `preprocessing_version`. Mixed preprocessing version dalam bucket yang sama ditolak.

## Batas Transport

Receiver belum mengonfigurasi parameter E32 over-air. Ia mengonsumsi newline JSON dari transparent serial/bridge. Channel/address/air-rate/CRC/link budget/range/packet loss adalah hardware integration task.
