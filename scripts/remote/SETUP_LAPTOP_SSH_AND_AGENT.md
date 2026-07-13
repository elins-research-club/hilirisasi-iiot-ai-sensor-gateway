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

## Opsi B (penuh): OpenSSH Server di Windows + Tailscale

1. Laptop online di Tailscale (`grey`).
2. Di PowerShell Admin:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name "OpenSSH-Server-In-TCP-Tailscale" -DisplayName "OpenSSH Server (Tailscale)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -RemoteAddress 100.64.0.0/10
```

3. Copy public key VPS ke Windows:

```powershell
# di Windows, buat C:\Users\rangg\.ssh\authorized_keys berisi isi:
# /home/ubuntu/.ssh/oracle_to_aws_ed25519.pub  (atau generate dedicated key)
```

4. Dari VPS:

```bash
ssh -i ~/.ssh/oracle_to_aws_ed25519 rangg@100.85.110.65 "py -3.13 --version"
```

Catatan: Tailscale native SSH **tidak support Windows**; butuh OpenSSH Server biasa.

## Rekomendasi

- **Sekarang:** Opsi A (job queue) — zero firewall drama, jalan lewat Syncthing.
- **Nanti:** Opsi B bila butuh interactive shell real-time.
