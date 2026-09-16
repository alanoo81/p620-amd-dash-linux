#!/usr/bin/env python3
"""HTTP proxy for AQC107 DASH (WS-Man on 623) that normalises the Digest challenge.

The firmware sends `WWW-Authenticate: Digest Nonce="..",Realm="..",Qop="auth"`; some clients
(Python urllib, AMD Management Console / Openwsman on Windows) do not recognise the capitalised
parameter names and give up. The proxy rewrites them to `realm=`, `nonce=`, `qop=` — the digest
computation only uses the values, so authentication is unchanged end to end.

It also maps the non-standard profile InstanceID "CIM:Boot Control:1.0.2" to
"CIM:BootControl:1.0.2" (and back in requests), so AMC recognises the Boot Control profile.

usage: dash-auth-proxy.py <listen-ip> <listen-port> <dash-ip> [<dash-port>]

  DASH_PROXY_LOG=file   one line per WS-Man exchange (action, class, HTTP code, ReturnValue, faults)
  DASH_PROXY_DUMP=file  full request/response of state-changing calls

Run it on a machine that reaches the DASH IP through the wire, NOT on the managed host itself
(the host cannot talk to its own NIC firmware). Point the client at <listen-ip>, HTTP only.
Tested with AMD Management Console 14 and AMD DASH CLI 9.0: inventory, health, event log, indications, text
redirection enumeration/connect work through it.
"""
import re, socket, sys, threading, time, os

LISTEN, LPORT, UP = sys.argv[1], int(sys.argv[2]), sys.argv[3]
UPORT = int(sys.argv[4]) if len(sys.argv) > 4 else 623

def read_message(sock, buf):
    while b"\r\n\r\n" not in buf:
        d = sock.recv(65536)
        if not d:
            return None, buf
        buf += d
    head, _, rest = buf.partition(b"\r\n\r\n")
    m = re.search(rb"(?im)^content-length:\s*(\d+)", head)
    n = int(m.group(1)) if m else 0
    while len(rest) < n:
        d = sock.recv(65536)
        if not d:
            break
        rest += d
    return head + b"\r\n\r\n" + rest[:n], rest[n:]

def fix_challenge(msg):
    head, sep, body = msg.partition(b"\r\n\r\n")
    def repl(m):
        params = re.sub(rb'\s*,\s*', b", ", m.group(2))
        params = re.sub(rb'(?i)\b(realm|nonce|qop|opaque|algorithm|stale|domain)=',
                        lambda k: k.group(1).lower() + b"=", params)
        return m.group(1) + params
    head = re.sub(rb"(?im)^(WWW-Authenticate:\s*Digest\s+)(.*)$", repl, head)
    return head + sep + body

LOG = os.environ.get("DASH_PROXY_LOG")
def log_exchange(req, resp):
    if not LOG:
        return
    act = re.search(rb"Action[^>]*>[^<]*/([\w]+)</", req)
    res = re.search(rb"ResourceURI[^>]*>[^<]*/([\w*]+)</", req)
    code = re.search(rb"^HTTP/1\.[01] (\d+)", resp)
    fault = re.findall(rb"<(?:\w+:)?(?:Value|Text)[^>]*>([^<]+)</", resp) if b"Fault" in resp else []
    rv = re.search(rb"ReturnValue>(\d+)<", resp)
    items = len(re.findall(rb"<(?:\w+:)?Item>|<(?:\w+:)?Items>", resp))
    line = "%s %-28s %-40s HTTP %s len=%d%s%s\n" % (time.strftime("%H:%M:%S"),
        (act.group(1).decode() if act else ("Identify" if b"Identify" in req else "-")),
        (res.group(1).decode() if res else "-"), code.group(1).decode() if code else "?", len(resp),
        (" ReturnValue=" + rv.group(1).decode()) if rv else "", (" FAULT " + " / ".join(f.decode() for f in fault[:3])) if fault else "")
    with open(LOG, "a") as f:
        f.write(line)
    dump = os.environ.get("DASH_PROXY_DUMP")
    if dump and (b"RequestPowerStateChange" in req or b"ChangeBootOrder" in req or b"RequestStateChange" in req
                 or b"SetBootConfigRole" in req):
        with open(dump, "ab") as f:
            f.write(b"==== " + time.strftime("%H:%M:%S").encode() + b" REQUEST\n" + req + b"\n==== RESPONSE\n" + resp + b"\n\n")

def fix_body(msg, old, new):
    """Replace a token in the body and fix Content-Length (firmware uses a non-standard
    InstanceID "CIM:Boot Control:1.0.2"; AMC looks for "CIM:BootControl:")."""
    head, sep, body = msg.partition(b"\r\n\r\n")
    if old not in body:
        return msg
    body = body.replace(old, new)
    head = re.sub(rb"(?im)^content-length:\s*\d+", b"Content-Length: " + str(len(body)).encode(), head)
    return head + sep + body

def handle(client):
    up = None
    cbuf = b""
    try:
        while True:
            req, cbuf = read_message(client, cbuf)
            if req is None:
                return
            req = re.sub(rb"(?im)^host:[^\r\n]*", b"Host: " + UP.encode() + b":" + str(UPORT).encode(), req, count=1)
            req = fix_body(req, b"CIM:BootControl:", b"CIM:Boot Control:")
            if up is None:
                up = socket.create_connection((UP, UPORT), timeout=60)
                ubuf = b""
            up.sendall(req)
            resp, ubuf = read_message(up, ubuf)
            if resp is None:
                return
            log_exchange(req, resp)
            resp = fix_body(resp, b"CIM:Boot Control:", b"CIM:BootControl:")
            client.sendall(fix_challenge(resp))
            if re.search(rb"(?im)^connection:\s*close", resp):
                return
    except OSError:
        pass
    finally:
        client.close()
        if up:
            up.close()

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((LISTEN, LPORT))
srv.listen(16)
print(f"proxy {LISTEN}:{LPORT} -> {UP}:{UPORT}", flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
