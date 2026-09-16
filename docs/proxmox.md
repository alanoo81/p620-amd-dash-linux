# Proxmox VE notes

Tested on Proxmox VE 9.2 with kernel 7.0.14-17-pve, onboard AQC107 as the only uplink (`nic0`
enslaved to `vmbr0`).

## Headers and DKMS

```sh
apt install proxmox-default-headers dkms curl patch
```

`proxmox-default-headers` follows the default kernel series, so DKMS rebuilds `atlantic-dash` when
`proxmox-kernel-*` updates. Check after each kernel update, **before rebooting**:

```sh
dkms status atlantic-dash
d=$(mktemp -d); unmkinitramfs /boot/initrd.img-<new-kernel> $d; modinfo -F srcversion $(find $d -name 'atlantic.ko*')
```

If a future kernel changes the driver so that the patch no longer applies, DKMS fails and the stock
driver is used: the machine keeps its network, DASH keeps working in exclusive mode (it lives in the
NIC firmware), only `AqDashConfig`/`AqDashAgent` lose their netlink channel.

`BUILD_EXCLUSIVE_KERNEL="^7\.0\."` in `dkms.conf` prevents builds on other series; relax it once
tested.

## Bridge

- The host IP is on `vmbr0`, not on `nic0`: use `tools/dash-configure.sh`, which adds the address to
  `nic0` as a `/32` only while `AqDashConfig` runs.
- Hot-swapping the module removes `nic0` from the bridge; re-attach it with
  `ip link set nic0 up; ip link set nic0 master vmbr0` (see the revert script in the guide).
- Exclusive mode uses its own MAC/IP on the same wire. It does not interfere with the bridge or with
  guests.

## Containers

The `aq-dash` family is only registered in the initial network namespace and requires
`CAP_NET_ADMIN` there: LXC containers and VMs cannot reach it.

## `pve-firewall`

DASH traffic in exclusive mode is handled by the NIC firmware and never reaches the host network
stack, so the Proxmox firewall does not apply to it. Filter access to the DASH IP on your network
equipment.

## Wake-on-LAN

Keep it as a fallback: `ethtool nic0 | grep Wake-on` should show `g`. The P620 BIOS setting is
`WakeonLAN` (think-lmi attribute of the same name).
