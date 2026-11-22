"""Serve the running app over https on the local network, so phones can share their GPS location with /sos.

Phones only give a web page their location when it is https. This forwards https://<this laptop>:8443 to the app on
http://127.0.0.1:8000 (one server, so the dashboard and the phones share the same live state). The certificate is
self-signed: the first time, the phone shows a warning; choose Advanced -> Proceed (Android) or Show details -> visit this
website (iPhone). Local network only; nothing is sent to the internet.

    uvicorn app:app --port 8000             # as usual, in one terminal
    python tools/https_proxy.py             # in another; prints the link for phones
    sudo ufw allow 8443/tcp                 # once, if a firewall blocks the phone

Options: --port 8443 --target 127.0.0.1:8000 --certdir data/certs
"""
import argparse, asyncio, ipaddress, pathlib, socket, ssl, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent.parent

def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))          # picks the outgoing interface; sends nothing
            return s.getsockname()[0]
    except OSError:
        return None

def ensure_cert(certdir, ips):
    """A self-signed certificate for localhost and this machine's addresses, made with the openssl command (remade when the
    address changes, e.g. on another Wi-Fi network)."""
    certdir.mkdir(parents=True, exist_ok=True)
    crt, key, tag = certdir / "local.crt", certdir / "local.key", certdir / "local.ips"
    want = ",".join(sorted(ips))
    if crt.exists() and key.exists() and tag.exists() and tag.read_text() == want:
        return crt, key
    san = ",".join(["DNS:localhost"] + [f"IP:{i}" for i in sorted(ips)])
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-keyout", str(key), "-out", str(crt),
                    "-subj", "/CN=quake-map local demo", "-addext", f"subjectAltName={san}"], check=True, capture_output=True)
    key.chmod(0o600)
    tag.write_text(want)
    return crt, key

async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--target", default="127.0.0.1:8000")
    ap.add_argument("--certdir", default=str(HERE / "data" / "certs"))
    a = ap.parse_args()
    th, tp = a.target.rsplit(":", 1)
    ip = lan_ip()
    ips = {"127.0.0.1"} | ({ip} if ip and ipaddress.ip_address(ip) else set())
    try:
        crt, key = ensure_cert(pathlib.Path(a.certdir), ips)
    except (OSError, subprocess.CalledProcessError) as ex:
        sys.exit(f"Could not make a certificate with openssl: {ex}")
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(crt, key)

    async def handle(cr, cw):
        try:
            ur, uw = await asyncio.open_connection(th, int(tp))
        except OSError:
            cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                     b"The app is not running. Start it with: uvicorn app:app --port 8000\n")
            await cw.drain()
            cw.close()
            return
        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))

    async def run():
        srv = await asyncio.start_server(handle, "0.0.0.0", a.port, ssl=ctx)
        print(f"https proxy -> http://{a.target}")
        if ip:
            print(f"\n  Phone link:  https://{ip}:{a.port}/sos\n")
        print("  The phone warns about the certificate once: Advanced -> Proceed (Android), Show details -> visit this website (iPhone).")
        print(f"  If the phone cannot connect, allow the port once: sudo ufw allow {a.port}/tcp\n")
        async with srv:
            await srv.serve_forever()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
