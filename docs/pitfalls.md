# Pitfalls

Everything here was hit on the test machine. Symptoms first, then cause and fix.

## `failed to get family id of socket: 3`

```
Aquantia DASH Configuration Tool (Linux Dev-preview) v0.15.7 (bundle v31.0.7)
failed to get family id of socket: 3
Failed to create netlink connection
```

The in-kernel `atlantic` driver has no `aq-dash` generic netlink family. Load the patched module
([guide §4](guide.md#4-patched-atlantic-driver)). `strace` shows the tool asking `CTRL_CMD_GETFAMILY`
for `"aq-dash"`.

## Marvell AQtion driver does not build on 7.0

The DASH-capable driver from <https://github.com/Aquantia/AQtion> (2.5.5, last pushed 2024) fails
with ~15 API errors on 7.0 (`netif_napi_add`, `strlcpy`, `from_timer`, `del_timer_sync`,
`PCI_IRQ_LEGACY`, `u64_stats_fetch_begin_irq`, ethtool `keee`/`rxfh_param`/`ts_info`, sysfs
`bin_attribute`...). Porting only `aq_dash.c` onto the upstream driver is much smaller — see
[driver-port.md](driver-port.md).

## Module refuses to load: `insmod: ERROR ... Invalid parameters`

```
WARNING: net/netlink/genetlink.c:585 at genl_validate_ops+0x2f9/0x330
```

On recent kernels a new generic netlink family must not set `.validate` flags on ops unless it
declares `resv_start_op` above them. Fix used here: no `.validate`, and
`.resv_start_op = AQ_DASH_CMD_MAX` so the (legacy, non-strict) validation the tools expect is kept.

## Stock driver still loaded after reboot

`cat /sys/module/atlantic/taint` is empty instead of `OE`, `genl ctrl get name aq-dash` fails.

initramfs-tools embeds `drivers/net/*` by directory; udev loads the stock `atlantic.ko` from the
initramfs, before `/lib/modules/*/updates` or `extra` are reachable. Install
[`initramfs/atlantic-dash`](../initramfs/atlantic-dash) and run `update-initramfs -u`. Verify with:

```sh
d=$(mktemp -d); unmkinitramfs /boot/initrd.img-$(uname -r) $d
modinfo -F srcversion $(find $d -name 'atlantic.ko*'); modinfo -F srcversion atlantic
```

## `Adapter - IP Addr get failed! Cannot assign requested address`

```
Found 4 ASF power control operations
Adapter - IP Addr get failed! Cannot assign requested address
[!] FAILED to configure Host Settings in DASH (code: -22)!
```

`AqDashConfig` reads the IPv4 address of the NIC given with `-d`. On a bridged host (Proxmox `vmbr0`)
the address is on the bridge. Add it temporarily as a `/32` without route
(`ip addr add 192.168.1.50/32 dev nic0 noprefixroute`), configure, remove it —
`tools/dash-configure.sh` does exactly that.

## Shared mode: ports filtered, never answered

After a successful `AqDashConfig shared`, `show` keeps reporting `IP: 0.0.0.0`. From another host,
`nmap` reports 623/664 as `filtered` on the OS IP (the firmware intercepts them) but no connection
ever completes, with or without `AqDashAgent`, with the driver loaded or unbound.

Use **exclusive mode** with a dedicated IP and MAC. It is also the mode that keeps working when the
OS is down.

## Testing DASH from the host itself shows nothing

Packets from the host to its own NIC's addresses never leave the machine, so the NIC firmware never
sees them. Test from another machine. For a local test with a USB dongle, move a macvlan into a
network namespace:

```sh
ip netns add dashtest
ip link add dprobe link enx001122334455 type macvlan mode bridge
ip link set dprobe netns dashtest
ip -n dashtest link set lo up; ip -n dashtest link set dprobe up
ip -n dashtest addr add 192.168.1.53/24 dev dprobe
ip netns exec dashtest nmap -Pn -p 623,664 192.168.1.51
```

## Firmware flash froze the host

`atlflashupdate -s -a nic0` with the `atlantic` driver bound: complete freeze (no log after the start,
no network, no SSH). After a cold power cycle the NIC reported the new firmware and worked. Flash
with console access, and expect to need a power cycle anyway.

## TLS: `unsafe legacy renegotiation disabled`

```
SSL routines:final_renegotiate:unsafe legacy renegotiation disabled
```

The firmware TLS stack requires legacy renegotiation, disabled by default in OpenSSL 3:

```sh
openssl s_client -connect 192.168.1.51:664 -legacy_renegotiation -CAfile DASHCA.crt -verify_ip 192.168.1.51
```

For curl, point `OPENSSL_CONF` at a file with `Options = UnsafeLegacyRenegotiation`
(`tools/dashws.py --https` does this).

## WS-Man requests return HTTP 200 with an empty body, or Python gets 401

- A hand-written SOAP envelope without `wsman:MaxEnvelopeSize`, `OperationTimeout` and the
  `mustUnderstand` headers got `HTTP 200` with `Content-Length: 0`. Use complete envelopes
  (see `tools/dashws.py`).
- Python `urllib`'s Digest handler never authenticates: the firmware sends
  `WWW-Authenticate: Digest Nonce="...",Realm="AQC107 DASH",Qop="auth"` with capitalised parameter
  names. curl accepts them.
- `wsmancli`/openwsman is not packaged in Debian 13.

## `openssl ca`: certificate for the same subject already exists

Re-issuing a device certificate with the same CN fails silently in scripts
(`failed to update database`). Set `unique_subject = no` in `index.txt.attr` (done by
`tools/make-certs.sh`).

## Windows VM with the NIC passed through

The documented Lenovo workflow is Windows-only (`AqDashConfig.exe` talks to the Windows driver over
WMI). It is possible to provision from a Windows VM with the AQC107 passed through (it is alone in
its IOMMU group), but:

- the VM does not see the host's ACPI `ASF!` table: `Found 0 ASF power control operations`, so remote
  power control would not be programmed;
- injecting the host table into the VM (`-acpitable file=ASF.aml`) and starting it with the NIC
  passed through was followed within seconds by a **hard reset of the host** (seen once, cause not
  established — the previous boot of the same VM without the table was fine);
- the host loses that NIC while the VM runs.

With the patched driver, none of this is needed: provisioning from Linux programs the 4 ASF commands
from the real firmware tables.
