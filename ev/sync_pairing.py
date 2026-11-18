"""Short verification codes for locally approved LAN sync requests."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass

PAIRING_TTL = 120
MAX_REQUESTS = 16
HEX_TOKEN = re.compile(r"[a-f0-9]{64}")
REQUEST_ID = re.compile(r"[a-f0-9]{32}")


def commitment(nonce):
    return hashlib.sha256(bytes.fromhex(nonce)).hexdigest()


def verification_code(nonce, fingerprint, request_id, challenge):
    # The receiver chooses its challenge after the sender commits to the nonce.
    transcript = f"expvault-pair-v1:{fingerprint}:{request_id}:{challenge}".encode("ascii")
    digest = hmac.digest(bytes.fromhex(nonce), transcript, "sha256")
    return f"{int.from_bytes(digest, 'big') % 1_000_000:06d}"


@dataclass
class PairingRequest:
    hostname: str
    address: str
    commitment: str
    challenge: str
    deadline: float
    code: str = ""
    state: str = "pending"


class IncomingPairings:
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint
        self.requests = {}
        self.lock = threading.Lock()
        self.closed = False

    def reap(self):
        now = time.monotonic()
        self.requests = {key: item for key, item in self.requests.items() if item.deadline > now}

    def start(self, address, payload):
        name, digest = payload.get("hostname"), payload.get("commitment")
        if (set(payload) != {"hostname", "commitment"} or not isinstance(name, str)
                or len(name) > 80 or any(not char.isprintable() for char in name)
                or not isinstance(digest, str) or not HEX_TOKEN.fullmatch(digest)):
            raise ValueError("配对请求无效")
        with self.lock:
            self.reap()
            if (self.closed or len(self.requests) >= MAX_REQUESTS
                    or sum(item.address == address and item.state == "pending" for item in self.requests.values()) >= 3):
                raise ValueError("接收电脑暂时无法接收新的配对请求")
            key, challenge = secrets.token_hex(16), secrets.token_hex(32)
            self.requests[key] = PairingRequest(name or address, address, digest, challenge,
                                                time.monotonic() + PAIRING_TTL)
            return {"id": key, "challenge": challenge}

    def exchange(self, action, payload, pairing):
        key, nonce = payload.get("id"), payload.get("nonce")
        if (set(payload) != {"id", "nonce"} or not isinstance(key, str) or not REQUEST_ID.fullmatch(key)
                or not isinstance(nonce, str) or not HEX_TOKEN.fullmatch(nonce)):
            raise ValueError("配对请求无效")
        with self.lock:
            self.reap()
            item = self.requests.get(key)
            if not item or not hmac.compare_digest(item.commitment, commitment(nonce)):
                return {"status": "expired"}
            if action == "cancel":
                del self.requests[key]
                return {"status": "cancelled"}
            if not item.code:
                item.code = verification_code(nonce, self.fingerprint, key, item.challenge)
            result = {"status": item.state}
            if item.state == "approved":
                result["pairing"] = pairing
            return result

    def pending(self):
        with self.lock:
            self.reap()
            return [{"id": key, "hostname": item.hostname, "address": item.address, "code": item.code}
                    for key, item in self.requests.items() if item.code and item.state == "pending"]

    def decide(self, key, approved):
        with self.lock:
            self.reap()
            item = self.requests.get(key)
            if not item or item.state != "pending" or not item.code:
                raise ValueError("配对请求已处理或已过期")
            item.state = "approved" if approved else "rejected"

    def close(self):
        with self.lock:
            self.closed = True
            self.requests.clear()
