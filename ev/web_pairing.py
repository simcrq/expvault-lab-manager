"""Restricted per-experiment phone receivers owned by the web management process."""
from __future__ import annotations

import secrets
import sys
import threading
import time
from functools import partial

from . import network
from .phone_import import PhoneReceiver
from .web_service import WebError


class WebPairing:
    def __init__(self, service, *, workspace_name=""):
        self.service = service
        self.workspace_name = workspace_name
        self.hotspot = network.WindowsHotspot()
        self.lock = threading.RLock()
        self.receivers = {}
        self.closed = False

    def interfaces(self, hotspot=None):
        addresses = {item["address"]: item for item in network.local_interfaces()}
        if hotspot:
            for item in hotspot.get("interfaces", []):
                if network.private_ipv4(item.get("address", "")):
                    addresses[item["address"]] = item
        return sorted(addresses.values(), key=lambda item: (not item.get("hotspot", False), item.get("name", ""), item["address"]))

    def connectivity(self, *, local=False, refresh_hotspot=False):
        hotspot = self.hotspot.status() if local and refresh_hotspot else None
        return {"windows": sys.platform == "win32", "local_controls": local and sys.platform == "win32",
                "interfaces": self.interfaces(hotspot), "hotspot": self.hotspot_public(hotspot) if hotspot else None}

    def hotspot_public(self, value):
        result = {key: item for key, item in value.items() if key not in ("password", "ssid")}
        if value.get("ok") and value.get("state") == "On" and value.get("ssid") and value.get("password"):
            result.update(ssid=value["ssid"], password=value["password"], wifi_qr=network.qr_svg(network.wifi_payload(value["ssid"], value["password"])))
        return result

    def change_hotspot(self, action):
        # Serialize OS controls with shutdown, including an in-flight start.
        with self.lock:
            if self.closed:
                raise WebError("网页服务正在关闭", status=503)
            if action == "settings":
                return self.hotspot.open_settings()
            if action not in ("start", "stop", "status"):
                raise WebError("热点操作无效")
            value = getattr(self.hotspot, action)()
            return {"hotspot": self.hotspot_public(value), "interfaces": self.interfaces(value), "windows": True, "local_controls": True}

    def reap(self):
        with self.lock:
            expired = [key for key, pair in self.receivers.items() if time.monotonic() >= pair["receiver"].expires_at]
            receivers = [self.receivers.pop(key)["receiver"] for key in expired]
        for receiver in receivers:
            receiver.close()

    def create(self, exp_id, host):
        self.reap()
        with self.lock:
            if self.closed:
                raise WebError("网页服务正在关闭", status=503)
            if host not in {item["address"] for item in self.interfaces()}:
                raise WebError("请选择电脑当前可用的局域网网卡地址")
            with self.service.context() as ctx:
                record = self.service.record(ctx, exp_id)
            for key, value in self.receivers.items():
                if value["exp_id"] == exp_id and value["receiver"].host == host:
                    return self.info(key)
            if len(self.receivers) >= 8:
                raise WebError("已有 8 个手机接收会话，请先关闭不再使用的二维码")
            pair_id = secrets.token_hex(16)
            pair = {"exp_id": exp_id, "received": 0, "counter_lock": threading.Lock()}
            def received(_item):
                with pair["counter_lock"]:
                    pair["received"] += 1
            receiver = PhoneReceiver(self.service.inbox, record["title"], host=host, exp_id=exp_id,
                                     workspace_name=self.workspace_name, on_received=received,
                                     direct_import=partial(self.service.import_phone_file, exp_id)
                                     if self.service.nas_mode == "writer" else None).start()
            pair["receiver"] = receiver
            self.receivers[pair_id] = pair
            return self.info(pair_id)

    def info(self, pair_id):
        self.reap()
        with self.lock:
            pair = self.receivers.get(pair_id)
            if not pair:
                raise WebError("手机接收会话不存在或已关闭", status=404)
            receiver = pair["receiver"]
            return {"id": pair_id, "exp_id": pair["exp_id"], "url": receiver.url, "qr_svg": network.qr_svg(receiver.url),
                    "received": pair["received"], "expires_in": max(0, int(receiver.expires_at-time.monotonic()))}

    def stop(self, pair_id):
        with self.lock:
            pair = self.receivers.pop(pair_id, None)
        if not pair:
            raise WebError("手机接收会话不存在或已关闭", status=404)
        pair["receiver"].close()
        return {"closed": True}

    def close(self):
        with self.lock:
            self.closed = True
            receivers = [pair["receiver"] for pair in self.receivers.values()]
            self.receivers.clear()
        for receiver in receivers:
            receiver.close()
        if self.hotspot.started_by_app:
            self.hotspot.stop()
