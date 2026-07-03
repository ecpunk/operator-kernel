#!/usr/bin/env bash
# Deploy systemd unit files from the repo into /etc/systemd/system/ and reload.
# Run with: sudo scripts/deploy_systemd.sh
# Or with sudo access: bash scripts/deploy_systemd.sh
#
# Any unit file present in the repo's systemd/ directory is deployed.
# Existing installed units are overwritten.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_SRC="${REPO_DIR}/systemd"
SYSTEMD_DEST="/etc/systemd/system"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

echo "Deploying systemd units from ${SYSTEMD_SRC} ..."

deployed=()
for unit_file in "${SYSTEMD_SRC}"/*.service "${SYSTEMD_SRC}"/*.timer; do
    [[ -f "${unit_file}" ]] || continue
    unit_name="$(basename "${unit_file}")"
    cp "${unit_file}" "${SYSTEMD_DEST}/${unit_name}"
    echo "  deployed: ${unit_name}"
    deployed+=("${unit_name}")
done

if [[ ${#deployed[@]} -eq 0 ]]; then
    echo "No .service or .timer files found in ${SYSTEMD_SRC}. Nothing deployed."
    exit 0
fi

systemctl daemon-reload
echo "daemon-reload complete."

echo ""
echo "Unit status:"
for unit_name in "${deployed[@]}"; do
    status="$(systemctl is-active "${unit_name}" 2>/dev/null || true)"
    echo "  ${unit_name}: ${status}"
done

echo ""
echo "Done. To test a one-shot service immediately:"
echo "  sudo systemctl start <service-name>"
