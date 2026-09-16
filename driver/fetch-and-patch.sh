#!/bin/sh
# Fetch the upstream atlantic driver matching a kernel version and apply the aq-dash patch.
#
#   fetch-and-patch.sh <kernelver> <destdir>
#
# <kernelver> is `uname -r` style (7.0.14-17-pve, 7.0.0-15-generic, ...). The upstream stable
# tag is derived from it (v7.0.14, v7.0); override with KTAG=v7.0.x. Set KSRC=/path/to/linux to
# use a local kernel tree instead of downloading from git.kernel.org.
set -eu
KVER=${1:?kernel version}
DEST=${2:?destination directory}
HERE=$(cd "$(dirname "$0")" && pwd)
PATCH="$HERE/0001-atlantic-add-aq-dash-generic-netlink-relay.patch"
[ -f "$PATCH" ] || PATCH="$HERE/../driver/0001-atlantic-add-aq-dash-generic-netlink-relay.patch"
REL=drivers/net/ethernet/aquantia/atlantic

if [ -z "${KTAG:-}" ]; then
	base=${KVER%%-*}                              # 7.0.14
	case "$base" in *.*.0) base=${base%.0} ;; esac # 7.0.0 -> 7.0
	KTAG=v$base
fi

FILES="Makefile aq_cfg.h aq_common.h aq_drvinfo.c aq_drvinfo.h aq_ethtool.c aq_ethtool.h
aq_filters.c aq_filters.h aq_hw.h aq_hw_utils.c aq_hw_utils.h aq_macsec.c aq_macsec.h aq_main.c
aq_main.h aq_nic.c aq_nic.h aq_pci_func.c aq_pci_func.h aq_phy.c aq_phy.h aq_ptp.c aq_ptp.h
aq_ring.c aq_ring.h aq_rss.h aq_utils.h aq_vec.c aq_vec.h hw_atl/hw_atl_a0.c hw_atl/hw_atl_a0.h
hw_atl/hw_atl_a0_internal.h hw_atl/hw_atl_b0.c hw_atl/hw_atl_b0.h hw_atl/hw_atl_b0_internal.h
hw_atl/hw_atl_llh.c hw_atl/hw_atl_llh.h hw_atl/hw_atl_llh_internal.h hw_atl/hw_atl_utils.c
hw_atl/hw_atl_utils.h hw_atl/hw_atl_utils_fw2x.c hw_atl2/hw_atl2.c hw_atl2/hw_atl2.h
hw_atl2/hw_atl2_internal.h hw_atl2/hw_atl2_llh.c hw_atl2/hw_atl2_llh.h
hw_atl2/hw_atl2_llh_internal.h hw_atl2/hw_atl2_utils.c hw_atl2/hw_atl2_utils.h
hw_atl2/hw_atl2_utils_fw.c macsec/MSS_Egress_registers.h macsec/MSS_Ingress_registers.h
macsec/macsec_api.c macsec/macsec_api.h macsec/macsec_struct.h"

rm -rf "$DEST"
mkdir -p "$DEST/$REL"
if [ -n "${KSRC:-}" ]; then
	echo ">> copying atlantic sources from $KSRC"
	cp -a "$KSRC/$REL/." "$DEST/$REL/"
else
	URL="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/$REL"
	echo ">> downloading atlantic sources for $KTAG from git.kernel.org"
	for f in $FILES; do
		mkdir -p "$DEST/$REL/$(dirname "$f")"
		curl -fsS --retry 5 --retry-delay 3 -o "$DEST/$REL/$f" "$URL/$f?h=$KTAG"
	done
fi

echo ">> applying $(basename "$PATCH")"
(cd "$DEST" && patch -p1 --no-backup-if-mismatch < "$PATCH")
echo ">> ready: $DEST/$REL"
