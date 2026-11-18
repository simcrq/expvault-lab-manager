"""Discover ExpVault relays with a bounded UDP broadcast on local interfaces."""
from __future__ import annotations

import json
import re
import secrets
import socket
import threading
import time

from .network import local_interfaces, private_ipv4

PORT = 37666
PREFIX = b"expvault-sync-discover-v1:"
MAX_PACKET = 1024
HEX = re.compile(r"[a-f0-9]{64}")


class DiscoveryResponder:
    def __init__(self, host, describe):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stopped = threading.Event()
        try:
            self.listener.bind(("", PORT))
            self.listener.settimeout(.2)
            self.sender.bind((host, 0))
        except OSError:
            self.listener.close()
            self.sender.close()
            raise
        self.describe = describe
        self.thread = threading.Thread(target=self.listen, name="expvault-discovery", daemon=True)
        self.thread.start()

    def listen(self):
        while not self.stopped.is_set():
            try:
                request, peer = self.listener.recvfrom(MAX_PACKET)
                if len(request) != len(PREFIX) + 16 or not request.startswith(PREFIX):
                    continue
                if peer[0] != "127.0.0.1" and not private_ipv4(peer[0]):
                    continue
                description = json.dumps(self.describe(), ensure_ascii=False).encode()
                self.sender.sendto(request + description, peer)
            except socket.timeout:
                continue
            except OSError:
                if self.stopped.is_set():
                    break

    def close(self):
        self.stopped.set()
        self.thread.join()
        self.listener.close()
        self.sender.close()


def discover(*, exclude=""):
    request = PREFIX + secrets.token_bytes(16)
    destinations = {"127.0.0.1", *(item["broadcast"] for item in local_interfaces(include_broadcast=True)
                                  if item.get("broadcast"))}
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        receiver.bind(("", 0))
        for destination in destinations:
            try:
                receiver.sendto(request, (destination, PORT))
            except OSError:
                continue
        deadline = time.monotonic() + 1.2
        while deadline > time.monotonic() and len(found) < 32:
            receiver.settimeout(max(.001, deadline - time.monotonic()))
            try:
                packet, peer = receiver.recvfrom(MAX_PACKET)
            except socket.timeout:
                break
            if not packet.startswith(request) or (peer[0] != "127.0.0.1" and not private_ipv4(peer[0])):
                continue
            try:
                value = json.loads(packet[len(request):])
                port, name = value["port"], value["hostname"]
                fingerprint = value["fingerprint"]
                if type(port) is not int or not 1 <= port <= 65535 or not isinstance(name, str) or len(name) > 80:
                    continue
                if not HEX.fullmatch(fingerprint):
                    continue
                url = f"https://{peer[0]}:{port}"
                if url != exclude:
                    found[url] = {"url": url, "hostname": name, "fingerprint": fingerprint, "approval": value.get("approval") is True}
            except (ValueError, KeyError, TypeError):
                continue
    return sorted(found.values(), key=lambda item: (item["hostname"], item["url"]))
