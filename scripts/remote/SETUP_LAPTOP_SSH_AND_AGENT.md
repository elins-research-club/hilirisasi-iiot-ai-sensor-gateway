# Hermes Remote Control Laptop (Windows)

Tujuan: Hermes di VPS bisa **menjalankan dan mengawasi** command di laptop CUDA.

## Opsi A (paling cepat, tanpa admin SSH): Job Queue via Syncthing

Repo sudah di-sync. Laptop menjalankan agent polling:

```powershell
cd C:\vscode\IIOT-Project\iiot-ai-sensor-gateway
powershell -ExecutionPolicy Bypass -File .\scripts\remote\laptop_agent.ps1
```

Hermes enqueue job dari VPS:

```bash
cd /home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway
python3 scripts/remote/enqueue_job.py --command "py -3.13 scripts\laptop_bakeoff_runner.py --device cuda --lanes fidas,sim --epochs 80 --batch-size 256"
```

Hasil: `jobs/outbox/<job_id>.result.json` + `jobs/logs/<job_id>.log` (sync balik ke VPS).

### Recovery tombstone Syncthing setelah cleanup

Bila dashboard Syncthing VPS menunjukkan `needDeletes` remote yang hanya berisi folder cache/legacy, samakan policy ignore lokal Windows lalu minta rescan:

```powershell
cd C:\vscode\IIOT-Project\iiot-ai-sensor-gateway
powershell -ExecutionPolicy Bypass -File .\scripts\remote\fix_syncthing_ignored_tombstones.ps1
```

Helper ini fail-closed bila folder ID/path tidak cocok. Ia hanya menulis `.stignore` untuk cache/build regenerable dan memanggil API Syncthing lokal untuk rescan; tidak menghapus source, dataset, model, archive, atau `.stversions`.

## Opsi B (fallback): Job Queue via Syncthing

Gunakan bila laptop online tetapi reverse SSH sedang tidak tersedia. Laptop menjalankan
`laptop_agent.ps1`, lalu VPS menulis job ke folder sync. Jalur ini lebih lambat dan tidak
interaktif, tetapi tetap berguna sebagai fallback.

```bash
python3 scripts/remote/enqueue_job.py --command "py -3.13 scripts\laptop_bakeoff_runner.py --device cuda --lanes fidas,sim --epochs 80 --batch-size 256"
```

## Opsi C (canonical untuk GPT/DevSpace): scoped SSH runner

OpenSSH Server, Tailscale-only firewall, admin authorized key, dan alias
`grey-laptop` sudah diprovisi. GPT tidak boleh menebak IP atau identity file; panggil
entrypoint project berikut dari workspace DevSpace:

```bash
python3 scripts/remote/devspace_laptop_exec.py probe
```

Probe memverifikasi host/user, repo sync Windows, GPU, Python, PyTorch CUDA, branch, dan
dirty count. Contoh eksekusi aktual di RTX 4050:

```bash
python3 scripts/remote/devspace_laptop_exec.py run \
  --repo iiot-ai-sensor-gateway -- \
  py -3.13 scripts\\probe_cuda.py
```

Dry-run orchestration canonical:

```bash
python3 scripts/remote/devspace_laptop_exec.py run \
  --repo iiot-ai-sensor-gateway -- \
  py -3.13 scripts\\laptop_bakeoff_runner.py \
    --device cuda --dry-run --lanes sim --seeds 42 \
    --skip-public-prepare --skip-lstm
```

Runner menggunakan argument array tanpa shell interpolation. Executable dibatasi ke
Python/pytest dan query `nvidia-smi`; `python -c`, PowerShell/cmd bebas, executable/path
absolut, traversal keluar repo, dan opsi mutating `nvidia-smi` ditolak. Ini guardrail agar
GPT menjalankan entrypoint project yang sudah direview, bukan sandbox terhadap perilaku
internal script Python tersebut. Output/artifact yang ditulis di repo Windows akan kembali
ke VPS melalui Syncthing.

Untuk training panjang di DevSpace, gunakan `exec_command`; jika mendapat `sessionId`,
poll dengan `write_stdin` daripada mengulang command.

## Provisioning OpenSSH Windows (sudah selesai; referensi recovery)

1. Laptop online di Tailscale (`grey`).
2. Di PowerShell Admin:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name "OpenSSH-Server-In-TCP-Tailscale" -DisplayName "OpenSSH Server (Tailscale)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -RemoteAddress 100.64.0.0/10
```

3. Public key dedicated VPS harus ada pada path efektif akun admin:

```powershell
# Akun rangg termasuk Administrators, jadi sshd memakai file ini:
C:\ProgramData\ssh\administrators_authorized_keys
```

4. Verifikasi authoritative dari VPS:

```bash
ssh -o BatchMode=yes grey-laptop "whoami; hostname"
```

Catatan: Tailscale native SSH **tidak support Windows**; butuh OpenSSH Server biasa.

## Rekomendasi

- **GPT/DevSpace:** Opsi C scoped SSH runner.
- **Fallback saat SSH unavailable:** Opsi A/B job queue via Syncthing.
