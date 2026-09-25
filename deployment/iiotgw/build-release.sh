#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "tracked worktree is dirty; commit before release" >&2
  exit 2
fi

SHA="$(git rev-parse HEAD)"
RELEASE_ID="$(printf '%.12s' "$SHA")"
MODEL_SRC="$ROOT/models/pi5/data_co2/fits/model.pt"
MANIFEST_REL="deployment/model-manifests/co2_fits_pi5_20260828.json"
if [[ $# -gt 0 ]]; then
  STAGE="$1"
else
  STAGE="$ROOT/.release-stage/$RELEASE_ID"
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"
git archive HEAD | tar -x -C "$STAGE"

mkdir -p "$STAGE/deployment/models"
cp "$MODEL_SRC" "$STAGE/deployment/models/co2_fits_pi5_20260828.pt"

python3 - "$STAGE" "$MANIFEST_REL" <<'PY'
import hashlib, json, pathlib, sys
stage = pathlib.Path(sys.argv[1])
manifest = json.loads((stage / sys.argv[2]).read_text())
model = stage / manifest["model_path"]
digest = hashlib.sha256(model.read_bytes()).hexdigest()
if digest != manifest["model_sha256"]:
    raise SystemExit(f"model sha mismatch: {digest} != {manifest['model_sha256']}")
release = {
    "schema": "iiot.ai_sensor.release_model_check.v1",
    "model_manifest": sys.argv[2],
    "model_sha256": digest,
}
(stage / "deployment" / "RELEASE_MODEL_CHECK.json").write_text(
    json.dumps(release, indent=2)
)
print(f"MODEL_SHA256={digest}")
PY

cat > "$STAGE/deployment/RELEASE.txt" <<EOF
release_id=$RELEASE_ID
git_sha=$SHA
created_from=$(git remote get-url origin)
EOF

echo "RELEASE_ID=$RELEASE_ID"
echo "STAGE=$STAGE"
