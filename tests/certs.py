#!/usr/bin/env python3
"""A throwaway CA so the simulator will trust the mock Brightwheel over HTTPS.

`xcrun simctl keychain <udid> add-root-cert` installs the CA into the device's
trust store, which is what lets Get Contents of URL talk to localhost without
certificate errors. iOS refuses leaf certificates without a subjectAltName, an
extendedKeyUsage of serverAuth, or with a lifetime beyond ~398 days, so all
three are set here deliberately.

The generated key material is disposable and gitignored. Erasing the simulator
drops the trust, so the harness re-adds it after any erase.
"""
import subprocess
from pathlib import Path

TLS_DIR = Path(__file__).parent / "tls"

CA_CNF = """[req]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no
[dn]
CN = Brightwheel Test CA
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
"""

LEAF_CNF = """[req]
distinguished_name = dn
prompt = no
[dn]
CN = localhost
[v3_req]
basicConstraints = CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost,IP:127.0.0.1
"""


def _openssl(*args):
    subprocess.run(["openssl", *args], check=True,
                   capture_output=True, text=True)


def ensure_certs(force=False):
    """Return (ca_pem, server_pem), generating them the first time."""
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    ca_pem = TLS_DIR / "ca.pem"
    server_pem = TLS_DIR / "server.pem"
    if server_pem.exists() and ca_pem.exists() and not force:
        return ca_pem, server_pem

    (TLS_DIR / "ca.cnf").write_text(CA_CNF)
    (TLS_DIR / "leaf.cnf").write_text(LEAF_CNF)
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(TLS_DIR / "ca.key"), "-out", str(ca_pem),
             "-days", "300", "-config", str(TLS_DIR / "ca.cnf"))
    _openssl("req", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(TLS_DIR / "server.key"),
             "-out", str(TLS_DIR / "server.csr"),
             "-config", str(TLS_DIR / "leaf.cnf"))
    _openssl("x509", "-req", "-in", str(TLS_DIR / "server.csr"),
             "-CA", str(ca_pem), "-CAkey", str(TLS_DIR / "ca.key"),
             "-CAcreateserial", "-out", str(TLS_DIR / "server.crt"),
             "-days", "300", "-extfile", str(TLS_DIR / "leaf.cnf"),
             "-extensions", "v3_req")
    server_pem.write_text((TLS_DIR / "server.key").read_text()
                          + (TLS_DIR / "server.crt").read_text())
    return ca_pem, server_pem
