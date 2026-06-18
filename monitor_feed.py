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
        self.on_update = None   # callable(crew_dict)
        self.on_remove = None   # callable(crew_id)  (optional)

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class MockFeed(CrewFeed):
    """Simulated crews looping around a centre point at ~1 Hz."""

    def __init__(self, center=(53.5461, -113.4912), crews=3, total_shelters=40):
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
                placed = min(self._total, int(now / 3) + i * 5)
                # Crew 2 simulates an RTK-float (degraded) fix to exercise the UI.
                fix = 5 if i == 1 else 4
                crew = {
                    "id": cid, "name": name,
                    "lat": lat, "lon": lon, "course": round(course, 1),
                    "fix": fix, "sats": 14, "hdop": 0.8,
                    "field": "North Quarter",
                    "placed": placed, "total": self._total,
                    "ts": time.time(),
                }
                if self.on_update:
                    self.on_update(crew)
            self._stop.wait(1.0)
