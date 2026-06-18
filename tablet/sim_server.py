"""Dev simulator for the Bee Tent Field PWA — NO external dependencies.

Mimics what the ESP32 will do in the field:
  * Serves the PWA static files over HTTP on :8000
  * Streams a simulated position object over a WebSocket on :8081 at ~2 Hz

The simulated unit walks a slow loop around the sample field so you can watch
the live marker, heading arrow, and Ground view behave before any hardware
exists. Fix quality cycles mostly RTK-fixed (4) with the odd float (5).

Run from the tablet/ folder:

    python sim_server.py

then open  http://localhost:8000  in a browser. Stop with Ctrl+C.
"""
import base64
import hashlib
import http.server
import json
import math
import os
import socket
import struct
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HTTP_PORT = 8000
WS_PORT = 8081
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Centre of the sample field; the unit drives a small circle around it.
CENTER_LAT, CENTER_LON = 53.5461, -113.4912
RADIUS_DEG = 0.0010  # ~110 m


# ---- minimal HTTP static server --------------------------------------------
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def log_message(self, *a):  # quiet
        pass


def serve_http():
    httpd = http.server.ThreadingHTTPServer(("", HTTP_PORT), Handler)
    print(f"  HTTP : http://localhost:{HTTP_PORT}")
    httpd.serve_forever()


# ---- minimal WebSocket server (server->client text frames only) ------------
def ws_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def ws_handshake(conn) -> bool:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(1024)
        if not chunk:
            return False
        data += chunk
    key = None
    for line in data.decode("latin1").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    if not key:
        return False
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {ws_accept_key(key)}\r\n\r\n"
    )
    conn.sendall(resp.encode())
    return True


def ws_frame(payload: bytes) -> bytes:
    header = bytearray([0x81])  # FIN + text opcode
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    return bytes(header) + payload


def simulate(t: float) -> dict:
    ang = (t * 0.15) % (2 * math.pi)
    lat = CENTER_LAT + RADIUS_DEG * math.sin(ang)
    lon = CENTER_LON + RADIUS_DEG * math.cos(ang) * 1.6
    # course = direction of travel (tangent to the circle)
    course = (math.degrees(ang) + 90) % 360
    fix = 5 if int(t) % 20 == 0 else 4  # mostly RTK fixed, occasional float
    return {
        "lat": round(lat, 8), "lon": round(lon, 8), "alt": 723.4,
        "fix": fix, "hdop": 0.8, "sats": 14,
        "speed_kmh": 6.0, "course": round(course, 1),
        "utc": time.strftime("%H%M%S"),
    }


def handle_ws(conn, addr):
    try:
        if not ws_handshake(conn):
            conn.close(); return
        print(f"  WS   : client {addr[0]} connected")
        t0 = time.time()
        while True:
            payload = json.dumps(simulate(time.time() - t0)).encode()
            conn.sendall(ws_frame(payload))
            time.sleep(0.5)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        conn.close()


def serve_ws():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", WS_PORT))
    srv.listen(5)
    print(f"  WS   : ws://localhost:{WS_PORT}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_ws, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    print("Bee Tent Field simulator")
    threading.Thread(target=serve_ws, daemon=True).start()
    try:
        serve_http()
    except KeyboardInterrupt:
        print("\nstopped")
