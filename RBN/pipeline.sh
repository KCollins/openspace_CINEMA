#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# RBN -> OpenSpace Fieldlines Pipeline
# ==============================================================================

# Optional QRZ XML API credentials (falls back to Skimmer DB + cty.dat if unset)
export QRZ_USER="${QRZ_USER:-}"
export QRZ_PASS="${QRZ_PASS:-}"

# Query parameters (aligned with RBN CSV / API fields)
START_TIME="2025-10-25T00:00:00Z"   # UTC ISO timestamp (e.g., CQ WW DX CW weekend)
END_TIME="2025-10-25T06:00:00Z"     # UTC ISO timestamp
BIN_MINUTES=15                      # Discrete time bucket size per .osfls frame

# Optional filters (leave empty "" to include all)
BANDS="40m,20m,15m,10m"             # Comma-separated: 160m,80m,60m,40m,30m,20m,17m,15m,12m,10m,6m,2m
TX_MODES="CW"                       # Comma-separated: CW,RTTY,FT8,FT4,PSK31
SPOT_TYPES="CQ"                     # Comma-separated RBN 'mode' field: CQ,DX,BEACON
DX_CALLS=""                         # Optional filter on transmitting station(s)
DE_CALLS=""                         # Optional filter on receiving skimmer(s)
MIN_SNR=""                          # Optional minimum dB (e.g., "5")
MAX_ARCS_PER_FRAME=3000             # Cap arcs per time bin to keep OpenSpace smooth

DATA_DIR="./data"
OSFLS_DIR="./output/osfls"
COLOR_DIR="./output/colortables"

mkdir -p "$DATA_DIR" "$OSFLS_DIR" "$COLOR_DIR"

echo "[1/3] Generating OpenSpace transfer function color tables..."
python3 generate_colortables.py --out-dir "$COLOR_DIR"

echo "[2/3] Fetching historical RBN data and building .osfls sequence..."
CMD=(
  python3 fetch_rbn_history.py
  --start "$START_TIME"
  --end "$END_TIME"
  --bin-minutes "$BIN_MINUTES"
  --data-dir "$DATA_DIR"
  --out-dir "$OSFLS_DIR"
  --max-arcs-per-frame "$MAX_ARCS_PER_FRAME"
)

[[ -n "$BANDS" ]]      && CMD+=(--bands "$BANDS")
[[ -n "$TX_MODES" ]]   && CMD+=(--tx-modes "$TX_MODES")
[[ -n "$SPOT_TYPES" ]] && CMD+=(--spot-types "$SPOT_TYPES")
[[ -n "$DX_CALLS" ]]   && CMD+=(--dx "$DX_CALLS")
[[ -n "$DE_CALLS" ]]   && CMD+=(--de "$DE_CALLS")
[[ -n "$MIN_SNR" ]]    && CMD+=(--min-snr "$MIN_SNR")

"${CMD[@]}"

echo "[3/3] Copying .asset and color tables alongside sequence..."
cp rbn_fieldlines.asset ./output/rbn_fieldlines.asset

echo "Done! Load ./output/rbn_fieldlines.asset in OpenSpace."