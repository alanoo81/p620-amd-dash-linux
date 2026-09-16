# Driver port: `aq-dash` on upstream `atlantic`

## How the Marvell tools talk to the firmware

`AqDashConfig` and `AqDashAgent` build the firmware RPC messages themselves (configuration,
certificates, ASF power commands read from the ACPI `ASF!` table, agent requests). The kernel only
**relays opaque payloads** between userspace and the firmware mailbox:

```
AqDashConfig / AqDashAgent
   │  generic netlink family "aq-dash", cmd AQ_DASH_CMD_FWREQ
   │  attrs: IFNAME, CMD_ID (SEND_DATA | GET_DATA), MSG_DATA, MSG_DATA_LEN
   ▼
atlantic: aq_dash_doit_fwreq()
   │  hw_atl_utils_fw_rpc_wait() / hw_atl_utils_fw_rpc_call()   (FW 1.x/4.x RPC mailbox)
   │  hw_atl_utils_fw_downld_dwords()                            (read the reply)
   ▼
AQC107 firmware 4.2.x (DASH)

Firmware → agent: the NIC service task (1 Hz) sends AQ_DASH_READ_REQUEST (0x31) and forwards
the reply on the multicast group "aq_dash_event".
```

Those RPC primitives already exist, unchanged, in the upstream driver. The port is therefore limited
to the netlink relay and a few hooks.

## Changes (see the patch)

| File | Change |
|---|---|
| `aq_dash.c` (new) | Netlink family, request relay, event polling |
| `aq_dash.h` (new) | Userspace ABI (attribute/command/message ids, must match the tools) + prototypes |
| `hw_atl/hw_atl_utils.h` | `struct fw_dash_req { u8 buffer[4096]; }` added to the `hw_atl_utils_fw_rpc` union |
| `aq_main.c` | Register/unregister the family in module init/exit |
| `aq_nic.c` | Call `aq_dash_process_events()` from the service task |
| `Makefile` | Build `aq_dash.o` |

### Why the RPC union must grow

`self->rpc` is the buffer copied to the firmware. Upstream, the union only holds the WoL and fw2x
offload structures; DASH messages (certificates in particular) are up to 4 KiB. The relay copies the
userspace payload into `self->rpc`, and upstream `hw_atl_utils_fw_rpc_wait()` bounds lengths with
`sizeof(self->rpc)`. Marvell's driver has this member; a port that only copies `aq_dash.c` onto
upstream would memcpy up to 4 KiB into a much smaller buffer.

### Differences from the Marvell 2.5.5 implementation

- Kernel-version `#ifdef`s removed; modern genetlink API (`genlmsg_put_reply`, family-level policy).
- `.resv_start_op = AQ_DASH_CMD_MAX` and no `.validate` flags (required on recent kernels, and keeps
  the non-strict validation the tools rely on — they send `IFNAME` without the length the policy
  expects, which the kernel logs as `attribute type 1 has an invalid length` and accepts).
- `GENL_ADMIN_PERM` on the request op (the original let any local user send raw firmware RPCs).
- `netnsok = false` kept: not reachable from containers.
- RPC exchanges done under `nic->fwreq_mutex`, like every other firmware access in upstream.
- Length checks on userspace data (`MSG_DATA_LEN` ≤ attribute length and ≤ `sizeof(rpc)`) and on the
  firmware reply size.
- Fixed a leaked header allocation and a reply skb sized with `NLMSG_DEFAULT_SIZE` (too small for
  4 KiB payloads).
- Event multicast skipped when nobody listens; polling errors logged at debug level instead of every
  second.
- Device check uses the driver name (`AQ_CFG_DRV_NAME`).

## Verifying a build

```sh
modinfo -F srcversion atlantic.ko         # compare with the loaded module:
cat /sys/module/atlantic/srcversion
nm atlantic.ko | grep aq_dash_            # aq_dash_nl_init, aq_dash_doit_fwreq, ...
```

The DKMS build (fetch from git.kernel.org + patch) produced a module with the same `srcversion` as
the hand-built one used for all the tests.
