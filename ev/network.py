"""Local network discovery, QR codes and Windows mobile-hotspot controls."""
from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
import threading
from pathlib import Path


def private_ipv4(value):
    try:
        ip = ipaddress.ip_address(value)
        return ip.version == 4 and ip.is_private and not (ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast)
    except ValueError:
        return False


def local_interfaces():
    import psutil

    result, seen = [], set()
    stats = psutil.net_if_stats()
    for name, entries in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for entry in entries:
            address = entry.address
            if private_ipv4(address) and address not in seen:
                seen.add(address)
                result.append({"address": address, "name": name, "hotspot": False})
    return sorted(result, key=lambda item: (item["name"], item["address"]))


def wifi_payload(ssid, password):
    if not isinstance(ssid, str) or not ssid or not isinstance(password, str) or not password:
        raise ValueError("无法读取热点名称和密码，请在 Windows 设置中检查热点属性")
    def escape(value):
        return "".join("\\"+char if char in '\\;,:"' else char for char in value)
    return f"WIFI:T:WPA;S:{escape(ssid)};P:{escape(password)};;"


def qr_svg(value):
    import qrcode

    qr = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(value)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    path = "".join(f"M{x} {y}h1v1h-1z" for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" role="img" aria-label="连接二维码">'
            f'<rect width="{size}" height="{size}" fill="white"/><path d="{path}" fill="#173b2c"/></svg>')


REASONS = {
    "NoConnectionProfile": "未找到可共享的网络，请先连接校园网、有线网络或 Wi-Fi。",
    "DisabledByGroupPolicy": "当前 Windows 组织策略不允许开启移动热点。",
    "DisabledBySystemCapability": "当前系统未授予自动控制热点的能力，请在系统设置中开启。",
    "DisabledByHardwareLimitation": "无线网卡或驱动不支持移动热点。",
    "DisabledByOperator": "当前网络提供方不允许共享连接。",
    "DisabledBySku": "当前 Windows 版本不支持移动热点。",
    "WiFiDeviceOff": "无线网卡已关闭，请先启用 Wi-Fi。",
    "MobileBroadbandDeviceOff": "移动网络设备未开启。",
    "NetworkLimitedConnectivity": "当前网络连接受限，请检查校园网认证或网络连接。",
    "OperationInProgress": "Windows 正在切换热点状态，请稍后刷新。",
    "TetheringNotAllowed": "Windows 不允许共享当前连接，请检查系统设置或校园网策略。",
    "OperationTimeout": "热点操作超时，请刷新实际状态后再试。",
    "WindowsApiUnavailable": "当前 Windows 无法自动控制移动热点，请使用系统热点设置。",
}


class WindowsHotspot:
    def __init__(self):
        self.lock = threading.RLock()
        self.started_by_app = False

    @property
    def supported(self):
        return sys.platform == "win32"

    def _invoke(self, action):
        if not self.supported:
            return {"ok": False, "state": "Unavailable", "reason": "NotWindows", "message": "一键开启热点仅支持 Windows；此设备可使用已有热点或局域网。", "interfaces": []}
        script = Path(__file__).resolve().parents[1] / "tools" / "windows_hotspot.ps1"
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Action", action],
                                    capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=40,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            value = json.loads(result.stdout.strip())
            if result.returncode != 0 or not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
                raise ValueError
            if not isinstance(value.get("interfaces", []), list):
                raise ValueError
        except subprocess.TimeoutExpired:
            value = {"ok": False, "reason": "OperationTimeout", "state": "Unknown", "interfaces": []}
        except (OSError, ValueError):
            value = {"ok": False, "reason": "WindowsApiUnavailable", "state": "Unknown", "interfaces": []}
        if value["ok"] and ((action == "start" and value.get("state") != "On") or (action == "stop" and value.get("state") != "Off")):
            value.update(ok=False, reason="OperationInProgress")
        reason = value.get("reason", "")
        value["message"] = REASONS.get(reason, "热点操作未完成，请在系统热点设置中检查。") if not value["ok"] else ("Windows 热点已开启" if value.get("state") == "On" else "Windows 热点未开启")
        value["interfaces"] = [item for item in value.get("interfaces", []) if isinstance(item, dict) and private_ipv4(item.get("address", ""))]
        return value

    def status(self):
        with self.lock:
            return {**self._invoke("status"), "started_by_app": self.started_by_app}

    def start(self):
        with self.lock:
            value = self._invoke("start")
            if value.get("ok") and value.get("state") == "On" and not value.get("was_on", True):
                self.started_by_app = True
            return {**value, "started_by_app": self.started_by_app}

    def stop(self):
        with self.lock:
            if not self.started_by_app:
                raise ValueError("这个热点并非由本次网页服务开启，请在 Windows 设置中自行关闭")
            value = self._invoke("stop")
            if value.get("ok") and value.get("state") == "Off":
                self.started_by_app = False
            return {**value, "started_by_app": self.started_by_app}

    def open_settings(self):
        if not self.supported:
            raise ValueError("系统热点设置入口仅支持 Windows")
        os.startfile("ms-settings:network-mobilehotspot")
        return {"opened": True}
