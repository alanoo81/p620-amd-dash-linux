# AMD DASH on the Lenovo ThinkStation P620 — Linux / kernel 7.0

Out-of-band management (DMTF DASH) for the **Lenovo ThinkStation P620**, configured and used
**entirely from Linux** — no Windows, no AMD Management Console — tested on a headless Proxmox VE host.

DASH on the P620 is implemented by the firmware of the **onboard Marvell (Aquantia) AQC107 10 GbE NIC**.
Lenovo only documents a Windows workflow, and the Linux tools shipped by Marvell need a
DASH-enabled `atlantic` driver that no longer builds on current kernels. This repository provides:

| Path | What |
|---|---|
| [`driver/`](driver) | Patch adding the `aq-dash` generic netlink relay to the **upstream** `atlantic` driver (Linux 7.0.x), plus a script that fetches the matching upstream sources and applies it |
| [`dkms/`](dkms) | DKMS configuration (rebuilds automatically on kernel updates) |
| [`initramfs/`](initramfs) | initramfs-tools hook — without it the stock `atlantic` from the initramfs wins |
| [`systemd/`](systemd) | Unit for Marvell's `AqDashAgent` |
| [`tools/dashws.py`](tools/dashws.py) | Minimal WS-Man client (identify, enumerate, **remote power-on**, power state requests) — python3 + curl |
| [`tools/make-certs.sh`](tools/make-certs.sh) | CA + TLS certificate for the DASH endpoint |
| [`tools/dash-configure.sh`](tools/dash-configure.sh) | Wrapper around `AqDashConfig` (handles bridged hosts) |
| [`tools/dash-auth-proxy.py`](tools/dash-auth-proxy.py) | Proxy fixing the firmware's non-standard Digest challenge — needed by AMD Management Console |
| [`docs/`](docs) | [Step-by-step guide](docs/guide.md), [**remote management & clients**](docs/remote-management.md), [pitfalls](docs/pitfalls.md), [Proxmox VE notes](docs/proxmox.md), [driver port details](docs/driver-port.md) |

## Status

Working end to end, including **remote power-on from S5**:

- WS-Man over HTTP (623) and HTTPS (664) with Digest authentication
- **Power-on from S5** validated; off and reset are **rejected by the firmware** (`ReturnValue 4`) — shut down / reboot from the OS
- Inventory: BIOS, NIC/EC firmware, CPU, memory, chassis, BIOS event log, boot sources
- AMD Management Console 14 works for inventory, health, logs through [`tools/dash-auth-proxy.py`](tools/dash-auth-proxy.py)
- Not working: KVM (AMD-specific classes missing), sensor readings (all `Unknown`); text console not validated yet

Tested configuration:

| Component | Version |
|---|---|
| Machine | ThinkStation P620 (30E1), BIOS S07KT67A, EC 1.19.0 |
| NIC | Onboard AQC107, PCI `1d6a:07b1` subsystem `17aa:1046` |
| NIC firmware | 4.2.46 DASH (Lenovo package `detn007fa.zip`) |
| Marvell Linux tools | AqDashConfig / AqDashAgent 0.15.7 |
| OS / kernel | Proxmox VE 9.2 (Debian 13), kernel 7.0.14-17-pve |

Other 7.0.x kernels should work (the patch touches very stable parts of the driver); other major
versions are untested — `BUILD_EXCLUSIVE_KERNEL` in `dkms.conf` limits DKMS to 7.0.

## Quick start

> Read [docs/guide.md](docs/guide.md) first: flashing NIC firmware and swapping the driver of what
> is often the only network interface of a headless machine can lock you out.

```sh
# 1. BIOS: enable DASH (think-lmi, no screen needed), then reboot
echo Enable > /sys/class/firmware-attributes/thinklmi/attributes/DASHSupport/current_value

# 2. Lenovo package (firmware 4.2.46 + Marvell Linux tools) — not redistributable, download it:
#    https://download.lenovo.com/pccbbs/thinkcentre_drivers/detn007fa.zip
#    flash only if `ethtool -i <nic>` reports an older 4.2.x (see guide), then cold power cycle

# 3. Patched atlantic driver via DKMS
apt install dkms curl patch proxmox-default-headers   # or linux-headers-$(uname -r)
sudo mkdir -p /usr/src/atlantic-dash-0.1.0
sudo cp dkms/dkms.conf driver/fetch-and-patch.sh driver/*.patch /usr/src/atlantic-dash-0.1.0/
sudo dkms install atlantic-dash/0.1.0
sudo install -m755 initramfs/atlantic-dash /etc/initramfs-tools/hooks/ && sudo update-initramfs -u
#    reboot (or hot-swap the module, see guide), then check:
genl ctrl get name aq-dash

# 4. Certificates and configuration (exclusive mode, dedicated DASH IP)
tools/make-certs.sh ./dash-certs myhost 192.168.1.51
sudo tools/dash-configure.sh exclusive nic0 admin 'S3cret!' dash-certs/cert.pem dash-certs/key.pem 192.168.1.51 02:00:00:00:00:51

# 5. From another machine (all clients and tasks: docs/remote-management.md)
tools/dashws.py -H 192.168.1.51 --https --cacert dash-certs/DASHCA.crt -u admin -p 'S3cret!' identify
tools/dashws.py -H 192.168.1.51 -u admin -p 'S3cret!' power 2      # power on
```

## Key findings

- **Use exclusive mode.** In shared mode on a bridged host the firmware intercepts 623/664 but never
  answers (it has no IP). Exclusive mode gives the firmware its own IP/MAC and works with the OS down.
- The Marvell `atlantic` driver 2.5.5 (the one with DASH support) does not build on 7.0; porting only
  the DASH part to the upstream driver is ~300 lines — but the firmware RPC buffer must be enlarged
  (4096 bytes): the upstream buffer is too small for DASH messages.
- On Linux, `AqDashConfig` reads the ACPI `ASF!` table and programs the 4 SMBus power-control
  commands. A Windows VM with the NIC passed through does **not** see that table.
- Test DASH **from another machine**: traffic from the host to its own NIC never reaches the wire.
- The firmware's Digest challenge is non-standard (`Nonce=`, `Realm=`, `Qop=`) and its nonce is
  constant: strict clients (AMC, Python urllib) never authenticate without the proxy.

Details, symptoms and fixes: [docs/pitfalls.md](docs/pitfalls.md).

## Disclaimer

Not affiliated with Lenovo, Marvell or AMD. Flashing NIC firmware and replacing a network driver on a
remote machine can make it unreachable; have physical or alternative access. Use at your own risk.

## License

GPL-2.0 (see [LICENSE](LICENSE)). The driver patch is derived from the Linux `atlantic` driver and
from the Marvell (Aquantia) AQtion driver, both GPL-2.0. Lenovo/Marvell firmware and binary tools are
**not** included — download them from Lenovo.
