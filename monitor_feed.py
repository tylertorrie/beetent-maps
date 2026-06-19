"""Crew position feed for the desktop Monitor view.

Abstraction over the live relay (Firebase / MQTT / custom WS — backend TBD).
A feed calls ``on_update(crew)`` for every position report. ``crew`` is a dict:

    {
      "id": "flag-1", "name": "Flagging Crew 1",
      "lat": 53.54, "lon": -113.49, "course": 247.5,
      "fix": 4, "sats": 14, "hdop": 0.8,
      "field": "North Quarter",
      "placed": 12, "total": 40,
      "ts": 1718650000.0      # epoch seconds, for staleness
    }

The real relay client will implement the same ``start()`` / ``stop()`` /
``on_update`` interface, so swapping it into the Monitor view is a one-liner.

``MockFeed`` simulates a few crews driving around a point so the Monitor view
can be built and demoed before any relay or hardware exists.

NOTE: ``on_update`` is invoked from a background thread — the Monitor view
marshals it onto the Tk main thread with ``after(0, ...)``.
"""
from __future__ import annotations

import math
import threading
import time


class CrewFeed:
    """Base interface. Subclasses fill in start()/stop()."""

    def __init__(self):
        self.on_update = None    # callable(crew_dict)
        self.on_remove = None    # callable(crew_id)   (optional)
        self.on_connect = None   # callable()  — fired once on first successful poll

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class MockFeed(CrewFeed):
    """Simulated crews looping around a centre point at ~1 Hz."""

    def __init__(self, center=(53.5461, -113.4912), crews=3, total_shelters=10):
        super().__init__()
        self._center = center
        self._n = crews
        self._total = total_shelters
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    def _run(self):
        clat, clon = self._center
        crews = [(f"flag-{i + 1}", f"Crew {i + 1}") for i in range(self._n)]
        t0 = time.time()
        while not self._stop.is_set():
            now = time.time() - t0
            for i, (cid, name) in enumerate(crews):
                ang = (now * 0.10 + i * 2.0) % (2 * math.pi)
                radius = 0.0008 + 0.0002 * i
                lat = clat + radius * math.sin(ang)
                lon = clon + radius * math.cos(ang) * 1.6   # lon scale fudge
                course = (math.degrees(ang) + 90) % 360
                placed = min(self._total, int(now / 5) + i)
                placed_ids = [f"S-{k:02d}" for k in range(1, placed + 1)]
                # Crew 2 simulates an RTK-float (degraded) fix to exercise the UI.
                fix = 5 if i == 1 else 4
                crew = {
                    "id": cid, "name": name,
                    "lat": lat, "lon": lon, "course": round(course, 1),
                    "fix": fix, "sats": 14, "hdop": 0.8,
                    "field": "North Quarter (sample)",
                    "field_file": "sample_field.geojson",
                    "placed": placed, "total": self._total,
                    "placed_ids": placed_ids,
                    "ts": time.time(),
                }
                if self.on_update:
                    self.on_update(crew)
            self._stop.wait(1.0)


class FirebaseFeed(CrewFeed):
    """Poll ``/{path}`` in a Firebase Realtime Database over plain REST GET —
    pure ``requests``, no SDK.

    We poll rather than use the SSE streaming endpoint: ``requests``' line
    iterator buffers the event stream and never surfaces it (curl gets the same
    events instantly, so it's a client-library quirk, not the server). A plain
    GET every ~1.5 s is rock-solid, trivially small for a handful of crews, and
    near-real-time. on_remove fires for crews that vanish between polls (a
    tablet's onDisconnect clears its node).

    db_url : e.g. "https://my-proj-default-rtdb.firebaseio.com"
    token  : optional auth token / database secret appended as ?auth=...
    path   : node holding the crews (default "crews").
    """

    def __init__(self, db_url, token=None, path="crews", interval=1.5):
        super().__init__()
        self._url = f"{db_url.rstrip('/')}/{path}.json"
        self._token = token or None
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._known = set()       # crew ids seen on the last poll, for removal detection
        self._connected = False   # becomes True after the first successful poll

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    def _run(self):
        import requests
        # A persistent Session is essential: opening a fresh TLS connection to
        # Firebase can take many seconds on some networks, but a kept-alive
        # connection polls in ~0.1 s. We pay the handshake once, then reuse it;
        # frequent polling also keeps the connection warm so it isn't dropped.
        session = requests.Session()
        params = {"auth": self._token} if self._token else None
        while not self._stop.is_set():
            try:
                r = session.get(self._url, params=params, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    present = set()
                    if isinstance(data, dict):
                        for cid, crew in data.items():
                            present.add(cid)
                            self._emit(cid, crew)
                    for gone in (self._known - present):
                        if self.on_remove:
                            self.on_remove(gone)
                    self._known = present
                    if not self._connected:
                        self._connected = True
                        if self.on_connect:
                            self.on_connect()
            except Exception:
                pass   # transient network error — next poll retries
            self._stop.wait(self._interval)

    def _emit(self, cid, crew):
        if not isinstance(crew, dict):
            return
        crew = dict(crew)
        crew.setdefault("id", cid)
        if self.on_update:
            self.on_update(crew)


class JsonPathFeed:
    """Poll one Firebase Realtime DB path over REST GET and hand the WHOLE JSON
    snapshot to ``on_data(data)`` each poll (data is the decoded node, or None).

    Used for the scans tree (``scans/<field>`` → {"shelters": {...}, "trays":
    {...}}). Same plain-``requests`` polling approach as FirebaseFeed; merging is
    idempotent so a repeated identical snapshot is harmless. ``on_data`` runs on a
    background thread — marshal onto the Tk main thread in the caller.
    """

    def __init__(self, db_url, path, token=None, interval=2.0):
        self.on_data = None
        self._url = f"{db_url.rstrip('/')}/{path.strip('/')}.json"
        self._token = token or None
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    def _run(self):
        import requests
        session = requests.Session()
        params = {"auth": self._token} if self._token else None
        while not self._stop.is_set():
            try:
                r = session.get(self._url, params=params, timeout=20)
                if r.status_code == 200 and self.on_data:
                    self.on_data(r.json())
            except Exception:
                pass   # transient network error — next poll retries
            self._stop.wait(self._interval)
