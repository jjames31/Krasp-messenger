#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${KAONIC_ROOT:-}"
INSTALL_DIR="${ROOT}/opt/kaonic-messenger"
CONFIG_DIR="${ROOT}/etc/kaonic-messenger"
DEFAULTS_FILE="${ROOT}/etc/default/kaonic-messenger"
SERVICE_FILE="${ROOT}/etc/systemd/system/kaonic-messenger.service"
COMMAND_FILE="${ROOT}/usr/local/bin/kaonic"

CALLSIGN="$(hostname -s)"
PORT=6969
PEERS=()
NON_INTERACTIVE=false
START_SERVICE=true

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Install and configure Kaonic Messenger on a Raspberry Pi.

Options:
  --callsign NAME       Sender/receiver callsign for this Pi
  --port PORT           UDP listening port (default: 6969)
  --peer NAME=HOST      Add a receiver; may be repeated
  --non-interactive     Accept defaults without prompting
  --no-start            Install files without enabling the service
  -h, --help            Show this help
EOF
}

while (($#)); do
    case "$1" in
        --callsign)
            CALLSIGN="${2:?--callsign requires a value}"
            shift 2
            ;;
        --port)
            PORT="${2:?--port requires a value}"
            shift 2
            ;;
        --peer)
            PEERS+=("${2:?--peer requires a value}")
            shift 2
            ;;
        --non-interactive)
            NON_INTERACTIVE=true
            shift
            ;;
        --no-start)
            START_SERVICE=false
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$ROOT" && "$EUID" -ne 0 ]]; then
    sudo_args=(--callsign "$CALLSIGN" --port "$PORT")
    for peer in "${PEERS[@]}"; do
        sudo_args+=(--peer "$peer")
    done
    [[ "$NON_INTERACTIVE" == true ]] && sudo_args+=(--non-interactive)
    [[ "$START_SERVICE" == false ]] && sudo_args+=(--no-start)
    exec sudo "$SCRIPT_DIR/install.sh" "${sudo_args[@]}"
fi

if [[ "$NON_INTERACTIVE" == false && -t 0 ]]; then
    read -r -p "Callsign for this Pi [$CALLSIGN]: " answer
    CALLSIGN="${answer:-$CALLSIGN}"
    read -r -p "UDP port [$PORT]: " answer
    PORT="${answer:-$PORT}"

    printf '\nAdd receivers as CALLSIGN=HOST[:PORT]. Press Enter when finished.\n'
    while true; do
        read -r -p "Receiver: " answer
        [[ -z "$answer" ]] && break
        PEERS+=("$answer")
    done
fi

python3 - "$SCRIPT_DIR" "$CALLSIGN" "$PORT" "${PEERS[@]}" <<'PY'
import sys

source_dir, callsign, port_text, *peers = sys.argv[1:]
sys.path.insert(0, source_dir)

from app import parse_peer, validate_callsign, validate_port

try:
    validate_callsign(callsign)
    port = int(port_text)
    validate_port(port)
    for peer in peers:
        parse_peer(peer)
except ValueError as error:
    raise SystemExit(f"Configuration error: {error}") from error
PY

if [[ -z "$ROOT" ]]; then
    if ! id -u kaonic >/dev/null 2>&1; then
        useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin kaonic
    fi
fi

install -d -m 0755 "$INSTALL_DIR" "$CONFIG_DIR" "$(dirname "$DEFAULTS_FILE")"
install -d -m 0755 "$(dirname "$SERVICE_FILE")" "$(dirname "$COMMAND_FILE")"
install -m 0644 "$SCRIPT_DIR/app.py" "$INSTALL_DIR/app.py"
install -m 0644 "$SCRIPT_DIR/protocol.py" "$INSTALL_DIR/protocol.py"
install -m 0644 "$SCRIPT_DIR/udp.py" "$INSTALL_DIR/udp.py"
install -m 0755 "$SCRIPT_DIR/kaonic" "$COMMAND_FILE"
install -m 0644 "$SCRIPT_DIR/kaonic-messenger.service" "$SERVICE_FILE"

printf 'KAONIC_CALLSIGN=%s\nKAONIC_PORT=%s\n' "$CALLSIGN" "$PORT" >"$DEFAULTS_FILE"
chmod 0644 "$DEFAULTS_FILE"

CONTACTS_FILE="$CONFIG_DIR/contacts.json"
if ((${#PEERS[@]})); then
    python3 - "$SCRIPT_DIR" "$CONTACTS_FILE" "$PORT" "${PEERS[@]}" <<'PY'
import json
from pathlib import Path
import sys

source_dir, output_path, default_port_text, *peer_texts = sys.argv[1:]
sys.path.insert(0, source_dir)

from app import parse_peer

default_port = int(default_port_text)
peers = {}
for peer_text in peer_texts:
    name, host, port = parse_peer(peer_text)
    peers[name] = {"host": host, "port": port or default_port}

Path(output_path).write_text(
    json.dumps({"peers": peers}, indent=2) + "\n",
    encoding="utf-8",
)
PY
elif [[ ! -f "$CONTACTS_FILE" ]]; then
    install -m 0644 "$SCRIPT_DIR/contacts.json" "$CONTACTS_FILE"
fi
chmod 0644 "$CONTACTS_FILE"

if [[ -z "$ROOT" && "$START_SERVICE" == true ]]; then
    systemctl daemon-reload
    systemctl enable kaonic-messenger.service
    systemctl restart kaonic-messenger.service

    if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
        ufw allow "${PORT}/udp"
    fi
fi

cat <<EOF

Kaonic Messenger is configured.
  Callsign: $CALLSIGN
  UDP port: $PORT
  Contacts: ${CONFIG_DIR}/contacts.json

Run: kaonic
Send once: kaonic send RECEIVER "message"
EOF
