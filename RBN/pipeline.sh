#!/usr/bin/env bash
set -euo pipefail

export QRZ_USER="${QRZ_USER:-}"
export QRZ_PASS="${QRZ_PASS:-}"

START_TIME="2024-05-10T00:00:00Z"
END_TIME="2024-05-15T06:00:00Z"
STEP_MINUTES=1                      # Advance 1 minute per .osfls frame
WINDOW_MINUTES=3                    # Each spot stays visible for 3 minutes, then disappears

BANDS="40m,20m,15m,10m"
TX_MODES="CW"
SPOT_TYPES="CQ"
DX_CALLS=""
DE_CALLS=""
MIN_SNR=""
MAX_ARCS_PER_FRAME=1500

DATA_DIR="./data"
OSFLS_DIR="./output/osfls"
COLOR_DIR="./output/colortables"

rm -rf "$OSFLS_DIR"
mkdir -p "$DATA_DIR" "$OSFLS_DIR" "$COLOR_DIR"

echo "[1/2] Generating OpenSpace transfer function color tables..."
python3 generate_colortables.py --out-dir "$COLOR_DIR"

echo "[2/2] Fetching historical RBN data and building .osfls sequence + .asset..."
CMD=(
  python3 fetch_rbn_history.py
  --start "$START_TIME"
  --end "$END_TIME"
  --step-minutes "$STEP_MINUTES"
  --window-minutes "$WINDOW_MINUTES"
  --data-dir "$DATA_DIR"
  --out-dir "$OSFLS_DIR"
  --asset-out "./output/rbn_fieldlines.asset"
  --max-arcs-per-frame "$MAX_ARCS_PER_FRAME"
)

[[ -n "$BANDS" ]]      && CMD+=(--bands "$BANDS")
[[ -n "$TX_MODES" ]]   && CMD+=(--tx-modes "$TX_MODES")
[[ -n "$SPOT_TYPES" ]] && CMD+=(--spot-types "$SPOT_TYPES")
[[ -n "$DX_CALLS" ]]   && CMD+=(--dx "$DX_CALLS")
[[ -n "$DE_CALLS" ]]   && CMD+=(--de "$DE_CALLS")
[[ -n "$MIN_SNR" ]]    && CMD+=(--min-snr "$MIN_SNR")

"${CMD[@]}"

echo "Done! Load ./output/rbn_fieldlines.asset in OpenSpace."
