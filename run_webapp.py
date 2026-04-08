import os
import socket

from dotenv import load_dotenv
import uvicorn


def local_ipv4_addresses() -> list[str]:
    seen: set[str] = set()
    addresses: list[str] = []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
            if host and not host.startswith("127."):
                seen.add(host)
                addresses.append(host)
    except OSError:
        pass

    try:
        host = socket.gethostbyname(socket.gethostname())
        if host and not host.startswith("127.") and host not in seen:
            seen.add(host)
            addresses.append(host)
    except OSError:
        pass

    return addresses


def main() -> None:
    load_dotenv()

    host = (os.getenv("WEBAPP_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    port = int((os.getenv("WEBAPP_PORT") or "8001").strip() or "8001")

    print(f"Starting Freight Quote Agent on {host}:{port}", flush=True)

    if host == "0.0.0.0":
        lan_addresses = local_ipv4_addresses()
        if lan_addresses:
            print("Open from other devices on the same Wi-Fi:", flush=True)
            for address in lan_addresses:
                print(f"  http://{address}:{port}", flush=True)
        print(f"Local machine: http://127.0.0.1:{port}", flush=True)
    else:
        print(f"Open this URL: http://{host}:{port}", flush=True)

    uvicorn.run("webapp.server:app", host=host, port=port)


if __name__ == "__main__":
    main()
