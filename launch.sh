#!/usr/bin/env bash
# launch.sh — Interactive launcher for the DELTA pipeline.
# Asks for folders/paths, then runs conversion inside delta.sif and validates output.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIF="/mnt/data/docker/custom/delta.sif"
BIDS_VALIDATOR="/home/akmisr@d.uzh.ch/.local/bin/bids-validator-deno"
STATE_FILE="$SCRIPT_DIR/delta_state.json"

# ── colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}╔══════════════════════════════════════════╗"
echo -e "║       DELTA Neuroimaging Pipeline        ║"
echo -e "║       DICOM → BIDS → Validate            ║"
echo -e "╚══════════════════════════════════════════╝${NC}"
echo ""

# ── check container exists ─────────────────────────────────────────────────────
if [[ ! -f "$SIF" ]]; then
    echo -e "${RED}ERROR: delta.sif not found at $SIF${NC}"
    echo "Build it first:  apptainer build delta.sif delta.def"
    exit 1
fi

# ── prompt helpers ─────────────────────────────────────────────────────────────
ask() {
    local prompt="$1" default="$2" answer
    if [[ -n "$default" ]]; then
        read -rp "$(echo -e "${YELLOW}${prompt}${NC} [${default}]: ")" answer
        echo "${answer:-$default}"
    else
        read -rp "$(echo -e "${YELLOW}${prompt}${NC}: ")" answer
        echo "$answer"
    fi
}

ask_yn() {
    local prompt="$1" default="${2:-y}" answer
    read -rp "$(echo -e "${YELLOW}${prompt}${NC} [y/n, default=${default}]: ")" answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy]$ ]]
}

# ── gather inputs ──────────────────────────────────────────────────────────────
echo -e "${CYAN}Step 1: Paths${NC}"
RAW_DIR=$(ask "Raw DICOM directory" "")
RAW_DIR="${RAW_DIR%/}"   # strip trailing slash

BIDS_DIR=$(ask "BIDS output directory" "$SCRIPT_DIR/bids")
BIDS_DIR="${BIDS_DIR%/}"

DCM2BIDS_CONFIG=$(ask "dcm2bids config JSON" "$SCRIPT_DIR/configs/dcm2bids_config.json")

echo ""
echo -e "${CYAN}Step 2: Subjects${NC}"
echo "Leave blank to convert ALL new subjects found under the raw directory."
SUBJECTS_INPUT=$(ask "Subject IDs to convert (space-separated, e.g. sub-01 sub-02)" "")

echo ""
echo -e "${CYAN}Step 3: Options${NC}"
FORCE=false
if ask_yn "Force re-convert already-converted subjects?" "n"; then
    FORCE=true
fi

DRY_RUN=false
if ask_yn "Dry run (show what would happen without converting)?" "n"; then
    DRY_RUN=true
fi

# ── validate inputs ────────────────────────────────────────────────────────────
echo ""
if [[ ! -d "$RAW_DIR" ]]; then
    echo -e "${RED}ERROR: DICOM directory not found: $RAW_DIR${NC}"
    exit 1
fi
if [[ ! -f "$DCM2BIDS_CONFIG" ]]; then
    echo -e "${RED}ERROR: dcm2bids config not found: $DCM2BIDS_CONFIG${NC}"
    echo "Run helper first to explore your DICOM headers:"
    echo "  apptainer run --app helper delta.sif --dicom <your_dicom_dir>"
    exit 1
fi

# ── build apptainer command ────────────────────────────────────────────────────
CONVERT_ARGS=(
    "--raw"    "$RAW_DIR"
    "--bids"   "$BIDS_DIR"
    "--config" "$DCM2BIDS_CONFIG"
    "--state"  "$STATE_FILE"
)

[[ "$FORCE"   == true ]] && CONVERT_ARGS+=("--force")
[[ "$DRY_RUN" == true ]] && CONVERT_ARGS+=("--dry-run")

if [[ -n "$SUBJECTS_INPUT" ]]; then
    CONVERT_ARGS+=("--subjects")
    # shellcheck disable=SC2206
    CONVERT_ARGS+=($SUBJECTS_INPUT)
fi

# ── summary before run ─────────────────────────────────────────────────────────
echo -e "${CYAN}─── Ready to run ───────────────────────────${NC}"
echo "  Raw DICOM : $RAW_DIR"
echo "  BIDS out  : $BIDS_DIR"
echo "  Config    : $DCM2BIDS_CONFIG"
echo "  State     : $STATE_FILE"
[[ -n "$SUBJECTS_INPUT" ]] && echo "  Subjects  : $SUBJECTS_INPUT" || echo "  Subjects  : all new"
[[ "$FORCE"   == true ]] && echo "  Force     : yes"
[[ "$DRY_RUN" == true ]] && echo "  Dry run   : yes"
echo ""

if ! ask_yn "Proceed?" "y"; then
    echo "Cancelled."
    exit 0
fi

# ── run conversion ─────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}─── Converting ─────────────────────────────${NC}"

apptainer exec \
    --bind "$RAW_DIR":"$RAW_DIR":ro \
    --bind "$BIDS_DIR":"$BIDS_DIR" \
    --bind "$SCRIPT_DIR":"$SCRIPT_DIR" \
    --bind "$SCRIPT_DIR/src:/opt/delta/src" \
    --bind "$SCRIPT_DIR/configs:/opt/delta/configs" \
    "$SIF" \
    python /opt/delta/src/cli.py convert "${CONVERT_ARGS[@]}"

CONVERT_EXIT=$?

if [[ $CONVERT_EXIT -ne 0 ]]; then
    echo -e "${RED}Conversion failed. See output above.${NC}"
    exit $CONVERT_EXIT
fi

# ── validate BIDS output ───────────────────────────────────────────────────────
if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}Dry run — skipping BIDS validation.${NC}"
    exit 0
fi

echo ""
echo -e "${CYAN}─── Validating BIDS output ─────────────────${NC}"

if [[ ! -x "$BIDS_VALIDATOR" ]]; then
    echo -e "${YELLOW}bids-validator-deno not found at $BIDS_VALIDATOR — skipping validation${NC}"
    exit 0
fi

"$BIDS_VALIDATOR" "$BIDS_DIR" --ignoreWarnings
VALID_EXIT=$?

if [[ $VALID_EXIT -eq 0 ]]; then
    echo -e "${GREEN}BIDS validation passed.${NC}"
else
    echo -e "${RED}BIDS validation found errors. Fix before preprocessing.${NC}"
    exit $VALID_EXIT
fi
