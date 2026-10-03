from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IIoTGWDeploymentTests(unittest.TestCase):
    def test_runtime_artifact_matches_manifest(self) -> None:
        manifest_path = (
            ROOT / "deployment/model-manifests/co2_fits_pi5_20260828.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = ROOT / manifest["runtime_artifact_path"]
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.assertEqual(digest, manifest["runtime_artifact_sha256"])
        self.assertEqual(manifest["runtime_backend"], "numpy_fits_v1")
        self.assertEqual(
            manifest["source_checkpoint_sha256"],
            "def7e300f77a2c06804cf781bd472b9d96092e4ed73415557188da278795755d",
        )
        self.assertEqual(manifest["deployment_mode"], "shadow_only")
        self.assertEqual(manifest["readiness"], "EXPERIMENTAL")
        self.assertEqual(manifest["runtime_cadence_compatibility"], "MISMATCH")
        self.assertNotEqual(
            manifest["training_data_cadence_sec_observed"],
            manifest["expected_cadence_sec"],
        )

    def test_gateway_deploy_does_not_pull_ml_or_cuda_stack(self) -> None:
        script = (ROOT / "deployment/iiotgw/deploy-via-tailscale.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('.[mqtt,edge,serial,dev]', script)
        self.assertNotIn('.[mqtt,ml]', script)
        self.assertNotIn("cuda", script.lower())
        self.assertIn("ssh -o BatchMode=yes", script)
        self.assertNotIn("pip install -e", script)

    def test_new_user_service_is_parallel_not_serial(self) -> None:
        unit = (ROOT / "systemd/user/iiot-ai-sensor-gateway.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("run-live-chirpstack", unit)
        self.assertIn("config/iiotgw.toml", unit)
        self.assertIn("ReadWritePaths=%h/.local/state/iiot-ai-sensor-gateway", unit)
        self.assertNotIn("/dev/tty", unit)
        self.assertNotIn("lora-packet-forwarder", unit)
        self.assertNotIn("chirpstack-node1-forwarder", unit)
        self.assertNotIn("chirpstack-node2-forwarder", unit)

    def test_usb_node_recovery_is_separate_and_narrow(self) -> None:
        ai_unit = (ROOT / "systemd/user/iiot-ai-sensor-gateway.service").read_text(
            encoding="utf-8"
        )
        usb_unit = (ROOT / "systemd/user/iiot-node-usb-release.service").read_text(
            encoding="utf-8"
        )
        helper = (
            ROOT / "deployment/iiotgw/recover_lorawan_usb_nodes.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Wants=iiot-node-usb-release.service", ai_unit)
        self.assertIn("Before=iiot-ai-sensor-gateway.service", usb_unit)
        self.assertIn("recover_lorawan_usb_nodes.py", usb_unit)
        self.assertIn("--passes 2", usb_unit)
        self.assertIn("--inter-pass-sec 5", usb_unit)
        self.assertIn("5926016290", helper)
        self.assertIn("58EF071105", helper)
        for forbidden in (
            "pinctrl",
            "lora_pkt_fwd",
            "chirpstack_to_emqx",
            "systemctl restart",
            "spidev",
        ):
            self.assertNotIn(forbidden, helper)

    def test_usb_recovery_deasserts_control_lines_and_does_not_write(self) -> None:
        path = ROOT / "deployment/iiotgw/recover_lorawan_usb_nodes.py"
        spec = importlib.util.spec_from_file_location("usb_recovery", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        class FakeSerial:
            def __init__(self):
                self.port = None
                self.baudrate = None
                self.timeout = None
                self.dtr = None
                self.rts = None
                self.exclusive = None
                self.is_open = False
                self._lines = [
                    b"rst:0x1 (POWERON_RESET)\n",
                    b"[LoRaWAN] Joining LoRaWAN Network...\n",
                ]

            def open(self):
                self.is_open = True

            def readline(self):
                return self._lines.pop(0) if self._lines else b""

            def close(self):
                self.is_open = False

        ticks = iter((0.0, 0.0, 0.1, 0.2, 1.1))
        fake = FakeSerial()
        result = module.recover_port(
            "/dev/fake",
            serial_factory=lambda: fake,
            hold_sec=1.0,
            monotonic=lambda: next(ticks),
        )
        self.assertFalse(fake.dtr)
        self.assertFalse(fake.rts)
        self.assertEqual(fake.baudrate, 115200)
        self.assertIn("POWERON_RESET", result["markers"])
        self.assertIn("Joining LoRaWAN Network", result["markers"])


if __name__ == "__main__":
    unittest.main()
