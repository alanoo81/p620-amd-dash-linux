# Managing the machine remotely

Once DASH is configured (exclusive mode, dedicated IP — `192.168.1.51` below), the machine can be
managed from any host on the management network, **whether the OS is running or the machine is
powered off** (standby power present).

Legend: ✅ tested on the reference machine · ⚠️ exposed by the firmware, not tested · ❌ not available

## What you can do

| Task | Status | How (with `tools/dashws.py`) |
|---|---|---|
| Check that DASH answers | ✅ | `identify` |
| Power state | ✅ | `enum CIM_AssociatedPowerManagementService` → `PowerState` 2 = on, 8 = off |
| **Power on** (from S5) | ✅ | `power 2` |
| Power off (soft) / reset | ❌ **rejected by the firmware** (`ReturnValue 4`) while the system is on, although listed in `AvailableRequestedPowerStates` — same result with AMD Management Console and AMD DASH CLI | `power 8` / `power 10` |
| Power cycle | ⚠️ not tested (most likely rejected like 8 and 10) | `power 5` |
| Hardware inventory | ✅ | `enum CIM_ComputerSystem`, `CIM_Processor`, `CIM_PhysicalMemory`, `CIM_Chassis` |
| Firmware versions | ✅ | `enum CIM_BIOSElement`, `CIM_SoftwareIdentity` (NIC firmware, EC) |
| Sensors | ⚠️ listed (voltages, temperatures) but every reading is 0 / Unknown on the test machine | `enum CIM_NumericSensor` |
| BIOS event log | ✅ | `enum CIM_RecordLog`, `CIM_LogEntry` |
| Boot sources / boot order | ✅ read | `enum CIM_BootSourceSetting`, `CIM_BootConfigSetting` |
| One-time boot / boot to BIOS setup | ⚠️ | Boot Control profile (DSP1012, one-time boot only); use DASH CLI or AMC |
| Text console (BIOS setup over SSH/Telnet) | ⚠️ service and access points can be enabled (`RequestStateChange` → 0); ports 22/23 answer a SYN but no session opens while the OS runs; not tested during POST | `CIM_TextRedirectionService`, `CIM_TextRedirectionSAP` (`txtsession0` Telnet 23, `txtsession1` SSH 22) |
| KVM (VNC) | ❌ access point listed, but `CIM_KVMRedirectionService` settings are empty and AMC requires the AMD-specific `AIMT_KVMCapabilities`, which this firmware does not implement | `CIM_KVMRedirectionSAP` |
| DASH accounts | ✅ read | `enum CIM_Account` (lockout after 10 failed logins) |
| Firmware update over DASH | ⚠️ | Software Update profile, NIC firmware only (AMC / DASH CLI + `AqDashAgent`) |
| Serial-over-LAN / IPMI | ❌ | not IPMI: DASH is WS-Management only |

Example session:

```sh
export DASH_HOST=192.168.1.51 DASH_USER=admin DASH_PASSWORD='S3cret!'

tools/dashws.py identify
tools/dashws.py enum CIM_AssociatedPowerManagementService | grep PowerState
tools/dashws.py power 2                                   # power on
tools/dashws.py --https --cacert DASHCA.crt enum CIM_SoftwareIdentity
```

## Recommended practices

- **Shut down and reboot from the OS** (`ssh host poweroff` / `reboot`): on this firmware DASH
  rejects off/reset requests while the system is on. Use DASH to **power the machine on**.
- **Wake-on-LAN stays a fallback** (`wakeonlan <host MAC>`), independent of the DASH configuration.
- Check `AvailableRequestedPowerStates` before scripting a transition: the firmware only accepts
  states valid from the current one (e.g. `5, 8, 10` when on).
- A request that is refused returns a non-zero `ReturnValue` (e.g. `4097`); `dashws.py` exits with
  status 1 in that case, so it can be used in scripts.

## Clients

### `tools/dashws.py` (this repository) — ✅ tested

- Linux, macOS, WSL; needs `python3` and `curl`.
- HTTP (623) or HTTPS (664, `--https --cacert DASHCA.crt`; enables the legacy TLS renegotiation the
  firmware requires, for its own requests only).
- Commands: `identify`, `enum <CIM class or resource URI>` (with pull), `power <state>`.
- Credentials from options, environment (`DASH_HOST`, `DASH_USER`, `DASH_PASSWORD`) or a
  `--creds` file (`user=` / `password=` lines).

### AMD DASH CLI (`dashcli`) — tested (v9.0, Windows), needs `tools/dash-auth-proxy.py`

AMD's command-line client, from AMD's
[manageability tools](https://www.amd.com/en/support/downloads/manageability-tools.html) page (an
older open-source DASH SDK with `dashcli` sources is mirrored at
[juergh/dash-sdk](https://github.com/juergh/dash-sdk)).

**Directly against the DASH IP it fails** like AMC: `discover` → "No system was identified as DASH
capable", other commands → "Unknown Error". The `-v 2` trace shows the 401 with the non-standard
Digest challenge, then retries. **Through the proxy** it works:

| Command (through the proxy) | Result |
|---|---|
| `discover` | ✅ |
| `-t computersystem[0] power status` | ✅ `Power state : On` |
| `enumerate bootconfig` | ✅ boot devices listed |
| `enumerate textredirection` | ✅ `txtsession0` = Telnet 23, `txtsession1` = SSH 22, "Enabled but Offline" |
| `-t computersystem[0] power reset` | ❌ "Power Operation failed" — same request as `dashws.py power 10`, firmware `ReturnValue 4` |

```bat
set D=-h 192.168.1.60 -p 623 -S http -a digest -u admin -P S3cret!
dashcli %D% discover
dashcli %D% -t computersystem[0] power status
dashcli %D% -t computersystem[0] power on
dashcli %D% enumerate textredirection
```

`-v 2 -o trace.txt` dumps the WS-Man exchanges.

### AMD Management Console (AMC) — tested (v14), needs `tools/dash-auth-proxy.py`

Windows GUI from the same AMD page, shown in Lenovo's P620 DASH guide.

**Directly against the DASH IP only discovery works.** AMC (built on Openwsman) receives the
firmware's Digest challenge `Digest Nonce="...",Realm="AQC107 DASH",Qop="auth"` and closes the
connection without ever sending credentials (verified with a packet capture): the capitalised
parameter names are not recognised. The firmware also advertises only the `https/digest` security
profile in `Identify`.

Through [`tools/dash-auth-proxy.py`](../tools/dash-auth-proxy.py), which rewrites the challenge
to `realm=`/`nonce=`/`qop=` and fixes the Boot Control profile id:

| AMC feature | Result |
|---|---|
| Discovery, inventory | ✅ |
| System health (sensors) | ✅ requests succeed, all sensors `Unknown` (firmware) |
| Event log, indications | ✅ |
| Text redirection enumerate / connect / disable | ✅ at WS-Man level; the terminal window reports "connection failed" (OS running) |
| KVM redirection, USB redirection | ❌ "enumeration failed" (`AIMT_KVMCapabilities` missing) |
| Power state 8 | ❌ "failed" (firmware `ReturnValue 4`) |

Setup: run the proxy on a Linux machine of the management LAN (not on the managed host),
e.g. `DASH_PROXY_LOG=amc.log tools/dash-auth-proxy.py 192.168.1.60 623 192.168.1.51`, then in AMC
*Global Configuration → Settings* keep **HTTP 623 only**, add a Digest authentication scheme with
the DASH credentials, and discover the proxy address.

### Generic WS-Management tools

- **curl + SOAP envelopes** — ✅ works (that is what `dashws.py` does). Use `--digest`; complete
  envelopes with `wsman:MaxEnvelopeSize`, `OperationTimeout` and `mustUnderstand` headers.
- **openwsman `wsman`** (`wsmancli`) — ⚠️ not packaged in Debian 13; may be available on other
  distributions.
- **Python** — `urllib`'s Digest handler cannot authenticate (the firmware capitalises `Nonce=`,
  `Realm=`, `Qop=`); shelling out to curl works, and so does `urllib` through
  `tools/dash-auth-proxy.py` (both tested).

### Endpoints summary

| Port | Protocol | Notes |
|---|---|---|
| 623/tcp | WS-Man over HTTP | Digest auth, realm `AQC107 DASH` |
| 664/tcp | WS-Man over HTTPS | TLS 1.2, legacy renegotiation required, certificate loaded with `AqDashConfig` |
| 22/tcp, 23/tcp | SSH / Telnet text console | endpoints defined, service disabled by default (filtered) |
| 5900/tcp | VNC KVM | access point defined, disabled by default |

## Security

- The DASH IP is a full management interface (power, boot, console). Put it on a management
  VLAN or filter it on your network equipment; the host firewall does not see this traffic.
- Port 623 carries Digest authentication in clear-text HTTP. Prefer 664 from untrusted segments,
  or restrict 623 to the management network.
- The firmware always sends the **same Digest nonce** (`dcd98b7102dd2f0e8b11d0f600bfb0c093`, the
  RFC 2617 example value), so a captured Digest response can be replayed. Treat 623 as
  unauthenticated from an attacker's point of view.
- Use a strong password: accounts are locked after 10 successive login failures
  (`MaximumSuccessiveLoginFailures`).
- `AqDashConfig` (run as root on the host) can reconfigure DASH at any time, including credentials:
  host root compromise means DASH compromise.
