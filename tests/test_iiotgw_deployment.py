from __future__ import annotations

import hashlib
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

    def test_gateway_deploy_does_not_pull_ml_or_cuda_stack(self) -> None:
        script = (ROOT / "deployment/iiotgw/deploy-via-tailscale.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('.[mqtt,edge,dev]', script)
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


if __name__ == "__main__":
    unittest.main()
