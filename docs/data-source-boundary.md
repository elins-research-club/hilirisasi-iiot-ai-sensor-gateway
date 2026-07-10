# Data Source Boundary

## Lane 1 — Simulation/Reference

Tujuan:

- regression test;
- parser/preprocessing development;
- model methodology exploration.

Contoh: simulator compact v2, UCI, Bristol, Zenodo reference, dan Gary historical.

Larangan:

- tidak boleh disebut field measurement;
- tidak boleh dicampur dengan real data tanpa lane/provenance;
- synthetic field tidak boleh menggantikan sensor yang tidak ada.

## Lane 2 — Real Offline Capture

Raw capture dari board/radio disimpan append-only sebelum adaptation. File raw tidak dinormalisasi atau ditimpa.

Metadata minimum:

- receive time;
- source/transport;
- node/boot/sequence bila dapat diparse;
- radio metadata bila tersedia;
- parse status;
- artifact checksum/commissioning notes bila diperlukan.

## Lane 3 — Real Live Receiver

Serial/replay receiver memproses frame saat runtime dan menulis raw/accepted/rejected/events. Accepted canonical tetap memiliki link ke raw line dan event ID.

## Canonical Boundary

Semua lane memakai canonical names hanya setelah adapter/parser resmi. Unit/source yang tidak setara tetap berada di `reference` atau migration lane.

## Modeling Boundary

Training/evaluation harus mencatat lane. Model yang bagus pada simulation/reference belum boleh dipromosikan ke real deployment.

## Storage/Git

- raw/canonical/model outputs berada di path gitignored;
- docs, adapter code, schemas, small fixtures, dan dataset cards boleh masuk Git;
- dataset besar, logs, checkpoint, dan credentials tidak boleh masuk Git.
