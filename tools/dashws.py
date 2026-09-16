#!/usr/bin/env python3
"""Minimal WS-Management client for DMTF DASH on the Marvell AQC107 (Lenovo ThinkStation P620).

Only needs python3 and curl (curl handles HTTP Digest: the AQC107 firmware sends
`Digest Nonce=..., Realm=...` with capitalised parameter names, which urllib rejects).

Examples:
  dashws.py -H 192.168.1.51 identify
  dashws.py -H 192.168.1.51 enum CIM_AssociatedPowerManagementService
  dashws.py -H 192.168.1.51 --https --cacert DASHCA.crt power 2

Credentials: --user/--password, DASH_USER/DASH_PASSWORD, or --creds FILE with
`user=...` and `password=...` lines.

Power states (DSP1027, CIM_PowerManagementService.RequestPowerStateChange):
  2 = on, 5 = power cycle (off soft), 8 = off (soft), 10 = master bus reset
  The firmware lists the states it accepts in AvailableRequestedPowerStates.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import uuid

CIM = "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/"
ANON = "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous"
ENUM = "http://schemas.xmlsoap.org/ws/2004/09/enumeration"

# The AQC107 TLS stack needs legacy renegotiation, which OpenSSL 3 disables by default.
OPENSSL_LEGACY = """openssl_conf = openssl_init
[openssl_init]
ssl_conf = ssl_sect
[ssl_sect]
system_default = system_default_sect
[system_default_sect]
Options = UnsafeLegacyRenegotiation
"""


def envelope(url, action, resource, body, selectors=""):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
        ' xmlns:wsman="http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd"'
        f' xmlns:wsen="{ENUM}">'
        f'<s:Header><wsa:To s:mustUnderstand="true">{url}</wsa:To>'
        f'<wsman:ResourceURI s:mustUnderstand="true">{resource}</wsman:ResourceURI>'
        f'<wsa:ReplyTo><wsa:Address s:mustUnderstand="true">{ANON}</wsa:Address></wsa:ReplyTo>'
        f'<wsa:Action s:mustUnderstand="true">{action}</wsa:Action>'
        '<wsman:MaxEnvelopeSize s:mustUnderstand="true">51200</wsman:MaxEnvelopeSize>'
        f'<wsa:MessageID s:mustUnderstand="true">uuid:{uuid.uuid4()}</wsa:MessageID>'
        f'<wsman:OperationTimeout>PT60S</wsman:OperationTimeout>{selectors}</s:Header>'
        f'<s:Body>{body}</s:Body></s:Envelope>'
    )


class Client:
    def __init__(self, args):
        scheme = "https" if args.https else "http"
        port = args.port or (664 if args.https else 623)
        self.url = f"{scheme}://{args.host}:{port}/wsman"
        self.user, self.password = credentials(args)
        self.args = args

    def post(self, xml):
        cmd = ["curl", "-s", "-m", str(self.args.timeout), "--digest",
               "-u", f"{self.user}:{self.password}",
               "-H", "Content-Type: application/soap+xml;charset=UTF-8",
               "--data-binary", "@-", "-w", "\n%{http_code}"]
        env = dict(os.environ)
        conf = None
        if self.args.https:
            conf = tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False)
            conf.write(OPENSSL_LEGACY)
            conf.close()
            env["OPENSSL_CONF"] = conf.name
            cmd += ["--cacert", self.args.cacert] if self.args.cacert else ["-k"]
        try:
            r = subprocess.run(cmd + [self.url], input=xml.encode(), capture_output=True, env=env)
        finally:
            if conf:
                os.unlink(conf.name)
        body, _, code = r.stdout.decode(errors="replace").rpartition("\n")
        if r.returncode:
            sys.exit(f"curl failed (exit {r.returncode}): {r.stderr.decode().strip()}")
        if code != "200":
            sys.exit(f"HTTP {code} from {self.url}" + (" (bad credentials?)" if code == "401" else ""))
        return body


def credentials(args):
    user = args.user or os.environ.get("DASH_USER")
    password = args.password or os.environ.get("DASH_PASSWORD")
    if args.creds:
        kv = dict(line.strip().split("=", 1) for line in open(args.creds) if "=" in line)
        user = user or kv.get("user")
        password = password or kv.get("password")
    if not user or not password:
        sys.exit("missing credentials (--user/--password, DASH_USER/DASH_PASSWORD or --creds)")
    return user, password


def show(xml, skip=("To", "Action", "RelatesTo", "MessageID", "EnumerationContext", "Address")):
    fault = re.search(r"<(?:\w+:)?Text[^>]*>([^<]+)<", xml)
    if "Fault" in xml and fault:
        print(f"FAULT: {fault.group(1)}")
    for m in re.finditer(r"<(?:\w+:)?(\w+)(?: [^>]*)?>([^<]{1,300})</", xml):
        if m.group(1) not in skip:
            print(f"  {m.group(1)} = {m.group(2)}")


def cmd_identify(c, _):
    show(c.post('<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
                ' xmlns:wsmid="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd">'
                '<s:Header/><s:Body><wsmid:Identify/></s:Body></s:Envelope>'))


def cmd_enum(c, args):
    res = args.resource if "://" in args.resource else CIM + args.resource
    xml = c.post(envelope(c.url, f"{ENUM}/Enumerate", res,
                          "<wsen:Enumerate><wsman:OptimizeEnumeration/>"
                          "<wsman:MaxElements>20</wsman:MaxElements></wsen:Enumerate>"))
    show(xml)
    ctx = re.search(r"EnumerationContext>([^<]+)<", xml)
    while ctx and "EndOfSequence" not in xml:
        xml = c.post(envelope(c.url, f"{ENUM}/Pull", res,
                              f"<wsen:Pull><wsen:EnumerationContext>{ctx.group(1)}</wsen:EnumerationContext>"
                              "<wsen:MaxElements>20</wsen:MaxElements></wsen:Pull>"))
        show(xml)
        ctx = re.search(r"EnumerationContext>([^<]+)<", xml)


def cmd_power(c, args):
    svc = CIM + "CIM_PowerManagementService"
    sel = ('<wsman:SelectorSet>'
           '<wsman:Selector Name="CreationClassName">CIM_PowerManagementService</wsman:Selector>'
           f'<wsman:Selector Name="Name">{args.service}</wsman:Selector>'
           '<wsman:Selector Name="SystemCreationClassName">CIM_ComputerSystem</wsman:Selector>'
           f'<wsman:Selector Name="SystemName">{args.system}</wsman:Selector></wsman:SelectorSet>')
    body = (f'<p:RequestPowerStateChange_INPUT xmlns:p="{svc}">'
            f'<p:PowerState>{args.state}</p:PowerState>'
            f'<p:ManagedElement><wsa:Address>{ANON}</wsa:Address><wsa:ReferenceParameters>'
            f'<wsman:ResourceURI>{CIM}CIM_ComputerSystem</wsman:ResourceURI><wsman:SelectorSet>'
            '<wsman:Selector Name="CreationClassName">CIM_ComputerSystem</wsman:Selector>'
            f'<wsman:Selector Name="Name">{args.system}</wsman:Selector></wsman:SelectorSet>'
            '</wsa:ReferenceParameters></p:ManagedElement></p:RequestPowerStateChange_INPUT>')
    xml = c.post(envelope(c.url, f"{svc}/RequestPowerStateChange", svc, body, sel))
    show(xml)
    rv = re.search(r"ReturnValue>(\d+)<", xml)
    if rv and rv.group(1) != "0":
        sys.exit(f"power state {args.state} rejected (ReturnValue {rv.group(1)})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-H", "--host", default=os.environ.get("DASH_HOST"), required="DASH_HOST" not in os.environ,
                   help="DASH IP address (env DASH_HOST)")
    p.add_argument("-P", "--port", type=int, help="port (default 623 http / 664 https)")
    p.add_argument("--https", action="store_true", help="use TLS on port 664")
    p.add_argument("--cacert", help="CA certificate to verify the DASH TLS certificate (else -k)")
    p.add_argument("-u", "--user")
    p.add_argument("-p", "--password")
    p.add_argument("--creds", default=os.environ.get("DASH_CREDS"), help="file with user= and password= lines")
    p.add_argument("--timeout", type=int, default=40)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("identify", help="WS-Man Identify").set_defaults(func=cmd_identify)
    e = sub.add_parser("enum", help="enumerate a CIM class (e.g. CIM_ComputerSystem)")
    e.add_argument("resource")
    e.set_defaults(func=cmd_enum)
    w = sub.add_parser("power", help="RequestPowerStateChange (2 on, 5 cycle, 8 off, 10 reset)")
    w.add_argument("state", type=int)
    w.add_argument("--service", default="power0")
    w.add_argument("--system", default="system0")
    w.set_defaults(func=cmd_power)
    args = p.parse_args()
    args.func(Client(args), args)


if __name__ == "__main__":
    main()
