#!/bin/sh
# Configure DASH on the onboard AQC107 with Marvell's AqDashConfig (Linux tool from the Lenovo
# package), working around the tool reading the IPv4 address of the NIC itself.
#
#   dash-configure.sh exclusive <nic> <user> <password> <cert.pem> <key.pem> <dash-ip> [<dash-mac>]
#   dash-configure.sh show
#   dash-configure.sh disable
#
# On bridged hosts (Proxmox vmbr0) the host IP lives on the bridge, not on the NIC, and
# AqDashConfig aborts with "Adapter - IP Addr get failed". The host address is added to the NIC
# as a /32 without route for the duration of the call, then removed.
# Requires the aq-dash atlantic module (genetlink family "aq-dash") and root.
set -eu
TOOL=${AQDASHCONFIG:-AqDashConfig}
command -v "$TOOL" >/dev/null 2>&1 || { echo "AqDashConfig not found (set AQDASHCONFIG=/path)" >&2; exit 1; }

case "${1:-}" in
show|disable) exec "$TOOL" "$1" ;;
exclusive|shared) ;;
*) sed -n '2,13p' "$0"; exit 1 ;;
esac

MODE=$1; NIC=$2; USER=$3; PASS=$4; CERT=$5; KEY=$6
if [ "$MODE" = exclusive ]; then
	DIP=${7:?DASH IP}; DMAC=${8:-}
fi

genl ctrl get name aq-dash >/dev/null 2>&1 || { echo "aq-dash netlink family missing: load the patched atlantic module" >&2; exit 1; }

HOSTIP=""
if [ -z "$(ip -4 -o addr show dev "$NIC")" ]; then
	master=$(basename "$(readlink "/sys/class/net/$NIC/master" 2>/dev/null)" 2>/dev/null || true)
	[ -n "$master" ] && HOSTIP=$(ip -4 -o addr show dev "$master" | awk '{print $4}' | cut -d/ -f1 | head -1)
	[ -n "$HOSTIP" ] || { echo "no IPv4 on $NIC or its bridge" >&2; exit 1; }
	ip addr add "$HOSTIP/32" dev "$NIC" noprefixroute
	trap 'ip addr del "$HOSTIP/32" dev "$NIC" 2>/dev/null' EXIT
fi

if [ "$MODE" = exclusive ]; then
	"$TOOL" exclusive "$USER" "$PASS" "$CERT" "$KEY" -d "$NIC" --ip "$DIP" ${DMAC:+--mac "$DMAC"}
else
	"$TOOL" shared "$USER" "$PASS" "$CERT" "$KEY" -d "$NIC"
fi
"$TOOL" show
if systemctl is-active -q aqdashagent 2>/dev/null; then systemctl restart aqdashagent; fi
