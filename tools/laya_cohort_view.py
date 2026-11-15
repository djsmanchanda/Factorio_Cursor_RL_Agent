# Path: tools/laya_cohort_view.py | Purpose: Temporary single-viewer UDP forwarding without rebinding campaign servers.
from __future__ import annotations

import select
import socket
import threading
import time


class ViewerRelay:
    """Forward one viewer to one inspected server; expire and recheck identity."""

    def __init__(self, bind, viewer, target, valid, *, duration=3600):
        self.viewer, self.valid = viewer, valid
        self.deadline = time.time() + duration
        self.client = None
        self.front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.back = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.front.bind((bind, 0))
            self.back.connect(target)
        except OSError:
            self.front.close()
            self.back.close()
            raise
        self.endpoint = "%s:%s" % self.front.getsockname()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        checked = 0
        try:
            while not self.stopped.is_set() and time.time() < self.deadline:
                if time.monotonic() - checked > 10:
                    if not self.valid():
                        break
                    checked = time.monotonic()
                for sock in select.select([self.front, self.back], [], [], .5)[0]:
                    packet, source = sock.recvfrom(65535)
                    if sock is self.front:
                        if source[0] != self.viewer or (self.client and source != self.client):
                            continue
                        self.client = source
                        self.back.send(packet)
                    elif self.client:
                        self.front.sendto(packet, self.client)
        except (OSError, ValueError):
            pass
        finally:
            self.stopped.set()
            self.front.close()
            self.back.close()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)

    def status(self):
        return {"endpoint": self.endpoint, "until": self.deadline,
                "active": not self.stopped.is_set(), "viewer": self.viewer,
                "reachability": "route unverified; requires matching GUI engine and mods"}
