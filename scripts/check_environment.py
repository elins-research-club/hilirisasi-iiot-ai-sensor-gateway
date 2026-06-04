from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from iiot_ai_sensor_gateway.config import load_config
from iiot_ai_sensor_gateway.parser import PayloadParser


def main() -> int:
    config = load_config(ROOT / 'config/default.toml')
    PayloadParser(config.identity.gateway_id, config.identity.default_room_id)
    print('environment ok')
    print(f'project root: {ROOT}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
