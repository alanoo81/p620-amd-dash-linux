# Step-by-step guide

Everything below is done over SSH on the P620 itself, except the final tests which must run from
**another machine on the same LAN**.

Addresses used as examples:

| | Example |
|---|---|
| Host IP (OS) | `192.168.1.50` |
| DASH IP (exclusive mode) | `192.168.1.51` |
| DASH MAC (locally administered) | `02:00:00:00:00:51` |
| Onboard NIC name | `nic0` (Proxmox VE 9 naming; often `enp1s0` elsewhere) |

## 0. Safety net

The onboard AQC107 is frequently the only NIC of the machine. Before touching firmware or driver:

- have physical access, a screen, or a **second network path** (a USB Ethernet dongle works fine),
- expect the host to become unreachable for a while during the firmware flash.

A USB dongle can be given its own address and routing table without disturbing the main network:

```sh
U=enx001122334455                 # your dongle
ip link set $U up
ip addr add 192.168.1.52/24 dev $U noprefixroute
ip route add 192.168.1.0/24 dev $U src 192.168.1.52 table 252
ip route add default via 192.168.1.1 dev $U table 252
ip rule add from 192.168.1.52 table 252
sysctl -w net.ipv4.conf.$U.arp_ignore=1 net.ipv4.conf.$U.arp_announce=2
```

## 1. Enable DASH in the BIOS (no screen needed)

The P620 BIOS settings are exposed by the `think-lmi` driver:

```sh
A=/sys/class/firmware-attributes/thinklmi/attributes
cat $A/DASHSupport/current_value        # Disable by default
echo Enable > $A/DASHSupport/current_value
cat $A/pending_reboot                   # 1
```

If a BIOS supervisor password is set, authenticate first through
`/sys/class/firmware-attributes/thinklmi/authentication/Admin/current_password`.
Reboot to apply.

## 2. Download the Lenovo package

`detn007fa.zip` from Lenovo ("Marvell Ethernet Firmware" for the P620):
<https://download.lenovo.com/pccbbs/thinkcentre_drivers/detn007fa.zip>

It contains:

- `atlflashupdate_1.8.0_4.2.46dash.tar.gz` — firmware 4.2.46 (DASH flavour) + Linux flash tool
- `DASH_Tool_Linux/` — `AqDashConfig`, `AqDashAgent`, `aqdashagent` .deb/.rpm
- `DASH_Tool_Windows/` — the Windows equivalents (not needed)

The package readme mentions a DASH-capable Linux driver "(source)" that is **not** in the zip; it is
Marvell's [AQtion](https://github.com/Aquantia/AQtion) driver, which does not build on 7.0 — hence
this repository.

## 3. Firmware

```sh
ethtool -i nic0 | grep firmware
```

The 4.2.x series is Lenovo's DASH firmware line for subsystem `17aa:1046`. If you already have
4.2.46, skip this step. 4.2.44 works as well; 4.2.46 fixes the OS "Quiesce" state and DHCP release in
exclusive mode with a static IP.

Flashing:

```sh
tar xzf atlflashupdate_1.8.0_4.2.46dash.tar.gz
cd atlflashupdate_1.8.0_4.2.46dash   # or wherever atlflashupdate is extracted
chmod +x atlflashupdate
./atlflashupdate -s -a nic0
```

> **Warning** — with the `atlantic` driver bound to the NIC, the flash **froze the whole host**
> (no log, no network) on the test machine. The firmware was nonetheless written correctly and
> reported 4.2.46 after a cold power cycle. Do it with console access, or unbind the driver first
> (`echo 0000:01:00.0 > /sys/bus/pci/drivers/atlantic/unbind`) — the latter is untested.

The tool asks for a **full power cycle** (shutdown, unplug AC ~30 s) to activate the new firmware.

## 4. Patched `atlantic` driver

Marvell's Linux tools talk to the driver over a generic netlink family named `aq-dash`. Without it:

```
failed to get family id of socket: 3
Failed to create netlink connection
```

### Build and install with DKMS (recommended)

```sh
apt install dkms curl patch             # + kernel headers (proxmox-default-headers on PVE)
mkdir -p /usr/src/atlantic-dash-0.1.0
cp dkms/dkms.conf driver/fetch-and-patch.sh driver/0001-*.patch /usr/src/atlantic-dash-0.1.0/
dkms install atlantic-dash/0.1.0
install -m755 initramfs/atlantic-dash /etc/initramfs-tools/hooks/
update-initramfs -u
```

`fetch-and-patch.sh` downloads the upstream driver for the running kernel's stable tag from
git.kernel.org (`7.0.14-17-pve` → `v7.0.14`) and applies the patch. Override with `KTAG=v7.0.x`, or
`KSRC=/path/to/linux` to use a local tree. DKMS may generate a MOK key to sign the module; with
Secure Boot enabled you must enroll it.

The **initramfs hook is required**: initramfs-tools copies `drivers/net/*` by directory, so the stock
`atlantic.ko` is embedded and loaded by udev before the root filesystem is mounted, and the DKMS
module is never used.

### Manual build

```sh
driver/fetch-and-patch.sh "$(uname -r)" /tmp/atlantic-dash
make -C /lib/modules/$(uname -r)/build \
     M=/tmp/atlantic-dash/drivers/net/ethernet/aquantia/atlantic CONFIG_AQTION=m modules
install -Dm644 /tmp/atlantic-dash/drivers/net/ethernet/aquantia/atlantic/atlantic.ko \
     /lib/modules/$(uname -r)/updates/atlantic.ko
depmod -a
```

### Activate

Reboot, or hot-swap the module. Hot-swapping cuts the network for a few seconds; use a script that
reverts to the stock module if the gateway does not answer (run it detached, e.g. with `setsid nohup`):

```sh
#!/bin/sh
KO=/lib/modules/$(uname -r)/updates/dkms/atlantic.ko   # adjust
GW=192.168.1.1
rmmod atlantic
if ! insmod "$KO"; then modprobe atlantic; fi
sleep 5; ip link set nic0 up; ip link set nic0 master vmbr0   # re-attach to the bridge if any
for i in $(seq 1 25); do ping -c1 -W1 $GW >/dev/null && exit 0; sleep 1; done
rmmod atlantic; modprobe atlantic; sleep 5; ip link set nic0 up; ip link set nic0 master vmbr0
```

Check:

```sh
cat /sys/module/atlantic/taint           # OE = out-of-tree module loaded
genl ctrl get name aq-dash                # family present, "requires admin permission"
AqDashConfig show
```

## 5. Marvell tools and agent

```sh
install -m755 DASH_Tool_Linux/AqDashConfig /usr/local/sbin/
install -m755 DASH_Tool_Linux/AqDashAgent  /usr/local/bin/
install -m644 systemd/aqdashagent.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now aqdashagent
```

The agent relays firmware requests (graceful power operations, OS status) to the host. Exclusive-mode
DASH itself runs in the NIC firmware.

## 6. Certificates

The firmware needs an **RSA 2048** certificate and unencrypted key. Put the DASH IP in the SANs:

```sh
tools/make-certs.sh ./dash-certs myhost 192.168.1.51
```

Import `dash-certs/DASHCA.crt` on the management consoles. Keep `private/cakey.pem` safe.
`AqDashConfig` warns `chains are not supported subject_alt_names.next != 0` — harmless.

## 7. Configure DASH (exclusive mode)

```sh
tools/dash-configure.sh exclusive nic0 admin 'S3cret!' dash-certs/cert.pem dash-certs/key.pem \
    192.168.1.51 02:00:00:00:00:51
```

Expected output:

```
Found 4 ASF power control operations
[*] DASH Host Settings successfully configured!
power command[0]: addr 0xa9, cmd 0x50, value 0
...
[*] DASH successfully configured!
- Mode: Exclusive
- MAC: 02:00:00:00:00:51
- IP: 192.168.1.51
```

The configuration is stored in the NIC flash and survives reboots and power cycles.

- Pick a free IP outside your DHCP range, and a locally administered MAC (`02:...`) different from
  the NIC MAC.
- `AqDashConfig` reads the IPv4 address of the NIC itself; on a bridged host it fails with
  `Adapter - IP Addr get failed!`. `dash-configure.sh` temporarily adds the host IP as a `/32`.
- Shared mode (same IP/MAC as the OS) did not work on a bridged host: see
  [pitfalls](pitfalls.md#shared-mode-ports-filtered-never-answered).

## 8. Test from another machine

```sh
nmap -Pn -p 623,664 192.168.1.51           # both open
tools/dashws.py -H 192.168.1.51 -u admin -p 'S3cret!' identify
tools/dashws.py -H 192.168.1.51 --https --cacert DASHCA.crt -u admin -p 'S3cret!' \
    enum CIM_AssociatedPowerManagementService
```

`identify` returns `DASHVersion = 1.1.0`, `ProductVendor = AQUANTIA`. Useful classes:
`CIM_ComputerSystem`, `CIM_AssociatedPowerManagementService`, `CIM_BIOSElement`,
`CIM_SoftwareIdentity`, `CIM_NumericSensor`, `CIM_Processor`, `CIM_PhysicalMemory`,
`CIM_TextRedirectionSAP`, `CIM_KVMRedirectionSAP`, `CIM_BootConfigSetting`.

## 9. Power control

```sh
tools/dashws.py -H 192.168.1.51 -u admin -p 'S3cret!' power 2     # on
tools/dashws.py -H 192.168.1.51 -u admin -p 'S3cret!' power 8     # off (soft)
tools/dashws.py -H 192.168.1.51 -u admin -p 'S3cret!' power 5     # power cycle
tools/dashws.py -H 192.168.1.51 -u admin -p 'S3cret!' power 10    # reset
```

`PowerState` in `CIM_AssociatedPowerManagementService`: 2 = on, 8 = off.
`AvailableRequestedPowerStates` lists what the firmware accepts in the current state. A rejected
request returns a non-zero `ReturnValue` (e.g. 4097).

Validated: clean OS shutdown, then `power 2` from another machine powers the P620 on.
Prefer a clean shutdown from the OS over `power 8` when the OS is reachable.

## Security notes

- Port 623 is plain HTTP (Digest auth). Keep the DASH IP on a trusted management network/VLAN.
- The TLS stack (664) requires legacy renegotiation and uses TLS 1.2 with DHE-RSA-AES128-SHA.
  `dashws.py --https` enables legacy renegotiation for its own curl calls only.
- The `aq-dash` netlink family is restricted to `CAP_NET_ADMIN` and not visible from other network
  namespaces (containers).
