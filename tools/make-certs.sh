#!/bin/sh
# Create a small CA and a DASH TLS certificate for the AQC107 firmware.
#
#   make-certs.sh <outdir> <dns-name> <ip> [<ip>...]
#
# The firmware only accepts RSA 2048 keys (unencrypted PEM). Put every address the DASH
# endpoint is reached on in the SANs (the DASH IP in exclusive mode). Re-running with an
# existing <outdir> keeps the CA and issues a new device certificate.
set -eu
OUT=${1:?outdir}; DNS=${2:?dns name}; shift 2
[ $# -ge 1 ] || { echo "at least one IP is required" >&2; exit 1; }
mkdir -p "$OUT/newcerts" "$OUT/private"; chmod 700 "$OUT/private"
cd "$OUT"
[ -f serial ] || echo 01 > serial
[ -f index.txt ] || : > index.txt
echo "unique_subject = no" > index.txt.attr

{
cat <<CNF
dir = .
[ ca ]
default_ca = CA_default
[ CA_default ]
serial = \$dir/serial
database = \$dir/index.txt
new_certs_dir = \$dir/newcerts
certificate = \$dir/DASHCA.crt
private_key = \$dir/private/cakey.pem
default_days = 3650
default_md = sha256
policy = policy_any
copy_extensions = none
[ policy_any ]
commonName = supplied
[ req ]
default_bits = 2048
default_md = sha256
distinguished_name = dn
prompt = no
[ dn ]
CN = $DNS
[ v3_ca ]
basicConstraints = critical, CA:TRUE
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
keyUsage = critical, keyCertSign, cRLSign
[ v3_req ]
basicConstraints = CA:FALSE
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
keyUsage = critical, digitalSignature, keyEncipherment, keyAgreement
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[ alt_names ]
DNS.1 = $DNS
CNF
i=1; for ip in "$@"; do echo "IP.$i = $ip"; i=$((i+1)); done
} > openssl.cnf

if [ ! -f DASHCA.crt ]; then
	openssl genrsa -out private/cakey.pem 2048 2>/dev/null
	openssl req -new -x509 -days 3650 -key private/cakey.pem -out DASHCA.crt \
		-config openssl.cnf -extensions v3_ca -subj "/CN=DASH Root CA ($DNS)"
fi
openssl req -new -nodes -newkey rsa:2048 -keyout key.pem -out req.pem -config openssl.cnf 2>/dev/null
openssl ca -batch -config openssl.cnf -extensions v3_req -in req.pem -out cert.full.pem 2>/dev/null
openssl x509 -in cert.full.pem -out cert.pem
rm -f cert.full.pem req.pem
chmod 600 key.pem
openssl verify -CAfile DASHCA.crt cert.pem
openssl x509 -in cert.pem -noout -ext subjectAltName
echo "device cert: $OUT/cert.pem  key: $OUT/key.pem  CA (import on consoles): $OUT/DASHCA.crt"
