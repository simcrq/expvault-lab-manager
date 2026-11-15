"""HTTPS WebDAV relay with immutable blobs and one independently published head per device.

Only the small heads collection is listed. History follows parent hashes, so a long
experiment history never depends on a provider's truncated directory listings.
"""
from __future__ import annotations

import base64
import email.utils
import hashlib
import http.client
import json
import os
import re
import ssl
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from filelock import FileLock

from .sync_merge import encoded
from .sync_transport import CHUNK, HEX, MAX_COMMIT

MAX_LIST = 2 * 1024**2
DEFAULT_URL = "https://dav.jianguoyun.com/dav/"


def device_id(root):
    """Stay independent even when a user copies a whole vault, including its settings."""
    from .nas import storage_root
    folder = storage_root() / "webdav/devices"
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    path = folder / (key + ".json")
    if path.is_symlink():
        raise ValueError("WebDAV 本机设备记录不能使用符号链接")
    with FileLock(str(path) + ".lock", timeout=10):
        if not path.exists():
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                stream.write(encoded({"id": uuid.uuid4().hex}))
        try:
            value = json.loads(path.read_bytes())["id"]
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError("WebDAV 本机设备记录损坏，请恢复该记录或移走后重启") from None
        return value


class WebDAVError(ValueError):
    def __init__(self, message, *, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def coordinates(url, username, password, folder):
    if not all(isinstance(value, str) for value in (url, username, password, folder)):
        raise ValueError("WebDAV 地址、账号、应用密码和目录必须是文字")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise ValueError("WebDAV 地址无效") from None
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or any(ord(c) < 33 for c in url) or len(url) > 2048):
        raise ValueError("WebDAV 必须使用不含账号、密码、查询参数的 HTTPS 地址")
    path = unquote(parts.path)
    if "\\" in path or any(c in path for c in "?#") or any(ord(c) < 32 for c in path) or any(p in (".", "..") for p in path.split("/")):
        raise ValueError("WebDAV 服务路径无效")
    folder = folder.strip().strip("/")
    if (not folder or len(folder) > 512 or "\\" in folder
            or any(p in ("", ".", "..") for p in folder.split("/"))
            or any(ord(c) < 32 or c in '<>:"|?*' for c in folder)):
        raise ValueError("请填写 WebDAV 内的相对同步目录，例如 ExpVault 或 科研/实验同步")
    if not username.strip() or len(username) > 320 or ":" in username or any(ord(c) < 32 for c in username):
        raise ValueError("请填写有效的 WebDAV 账号")
    if not password or len(password) > 4096 or any(ord(c) < 32 for c in password):
        raise ValueError("请填写 WebDAV 应用密码；坚果云请使用第三方应用密码")
    hostname = parts.hostname.encode("idna").decode("ascii").lower()
    authority = ("[" + hostname + "]" if ":" in hostname else hostname) + (":" + str(port) if port and port != 443 else "")
    endpoint = urlunsplit(("https", authority, quote(path.rstrip("/") + "/", safe="/"), "", ""))
    return endpoint, username.strip(), password, folder


class WebDAVRepository:
    def __init__(self, url, username, password, folder, *, cache, device, channel=None, stop=None):
        self.url, self.username, self.password, self.folder = coordinates(url, username, password, folder)
        self.parts = urlsplit(self.url)
        if not re.fullmatch(r"[a-f0-9]{32}", device):
            raise ValueError("WebDAV 设备编号无效")
        self.device, self.channel, self.cache = device, channel, cache
        self.stop = stop or threading.Event()
        self.origin = urlunsplit((self.parts.scheme, self.parts.netloc, "", "", ""))
        self.root = self.parts.path + "/".join(quote(p, safe="") for p in self.folder.split("/")) + "/expvault-webdav-v1/"
        self.authorization = "Basic " + base64.b64encode((self.username + ":" + self.password).encode()).decode("ascii")
        self.jianguoyun = self.parts.hostname == "dav.jianguoyun.com"
        self.spacing = 3.1 if self.jianguoyun else 0
        self.next_request = 0
        self.known_directories = set()
        self.head_cache = {}
        self.published = None
        self.present, self.absent = set(), set()

    def connection(self):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return http.client.HTTPSConnection(self.parts.hostname, self.parts.port or 443, context=context, timeout=30)

    def throttle(self):
        if self.stop.wait(max(0, self.next_request - time.monotonic())):
            raise InterruptedError("同步已停止")
        self.next_request = time.monotonic() + self.spacing

    def error(self, response):
        code = response.status
        if code in (401, 403):
            return WebDAVError("WebDAV 认证或权限失败，请核对账号、第三方应用密码和目录读写权限")
        if code in (429, 503):
            retry = response.getheader("Retry-After", "")
            try:
                seconds = int(retry)
            except ValueError:
                try:
                    seconds = (email.utils.parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    seconds = 1800
            return WebDAVError("WebDAV 服务限流或暂不可用，已保留本机数据，稍后自动重试", retry_after=min(86400, max(60, seconds)))
        if code == 413:
            return WebDAVError("文件超过 WebDAV 服务的单文件大小限制，请关闭照片或视频同步，或调整照片大小上限")
        if code == 507:
            return WebDAVError("WebDAV 云端空间不足，请释放空间后重试")
        if 300 <= code < 400:
            return WebDAVError("WebDAV 地址发生重定向；为保护应用密码未跟随，请填写最终的 HTTPS WebDAV 地址")
        return WebDAVError(f"WebDAV 请求失败（HTTP {code}），本机数据已保留")

    def request(self, method, path, *, data=None, stream=None, size=0, headers=None, accepted=(200,), progress=None, stop=None):
        self.throttle()
        connection = self.connection()
        try:
            connection.putrequest(method, path)
            for key, value in {"Authorization": self.authorization, "User-Agent": "ExpVault-WebDAV/1", **(headers or {})}.items():
                connection.putheader(key, value)
            if data is not None or stream is not None:
                connection.putheader("Content-Length", str(len(data) if data is not None else size))
            connection.endheaders()
            if data is not None:
                connection.send(data)
            if stream is not None:
                remaining = size
                while remaining:
                    if self.stop.is_set() or stop and stop.is_set():
                        raise InterruptedError("同步已停止")
                    block = stream.read(min(CHUNK, remaining))
                    if not block:
                        raise WebDAVError("本机同步对象读取中断")
                    connection.send(block)
                    remaining -= len(block)
                    if progress:
                        progress(len(block))
            response = connection.getresponse()
            if response.status not in accepted:
                raise self.error(response)
            return connection, response
        except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
            connection.close()
            raise WebDAVError("无法安全连接 WebDAV，请检查网络、HTTPS 证书和服务地址") from None
        except BaseException:
            connection.close()
            raise

    def small(self, method, path, *, maximum=MAX_LIST, **kwargs):
        connection, response = self.request(method, path, **kwargs)
        try:
            data = response.read(maximum + 1)
            if len(data) > maximum:
                raise WebDAVError("WebDAV 响应过大，已停止同步以保留本机数据")
            return response.status, data, response.getheader("ETag")
        finally:
            connection.close()

    def collection(self, path):
        if path not in self.known_directories:
            code, _, _ = self.small("MKCOL", path, accepted=(201, 405))
            if code == 405:
                self.propfind(path, depth=0)
            self.known_directories.add(path)

    def propfind(self, path, *, depth=1):
        _, data, _ = self.small("PROPFIND", path, data=b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getetag/></d:prop></d:propfind>',
                                headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"}, accepted=(207,))
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise WebDAVError("WebDAV 目录响应格式无效")
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            raise WebDAVError("WebDAV 目录响应格式无效") from None
        if root.tag != "{DAV:}multistatus":
            raise WebDAVError("WebDAV 目录响应格式无效")
        values = root.findall("{DAV:}response")
        if not values or len(values) >= 750:
            raise WebDAVError("WebDAV 设备目录可能被服务截断，已停止同步；请改用新的专用同步目录")
        if depth == 0:
            if not any(p.find("{DAV:}prop/{DAV:}resourcetype/{DAV:}collection") is not None
                       and re.search(r"\s200\s", p.findtext("{DAV:}status", ""))
                       for item in values for p in item.findall("{DAV:}propstat")):
                raise WebDAVError("WebDAV 服务地址不是可访问的文件夹")
        return values

    def channel_record(self, *, create=False):
        code, data, _ = self.small("GET", self.root + "channel.json", maximum=4096, accepted=(200, 404))
        if code == 404 and create:
            proposed = encoded({"protocol": 1, "transport": "webdav", "id": uuid.uuid4().hex})
            self.small("PUT", self.root + "channel.json", data=proposed, headers={"If-None-Match": "*", "Content-Type": "application/json"}, accepted=(200, 201, 204, 412))
            code, data, _ = self.small("GET", self.root + "channel.json", maximum=4096, accepted=(200, 404))
        try:
            record = json.loads(data) if code == 200 else None
            valid = (isinstance(record, dict) and record.get("protocol") == 1 and record.get("transport") == "webdav"
                     and re.fullmatch(r"[a-f0-9]{32}", record.get("id", "")))
        except (ValueError, TypeError):
            valid = False
        if not valid or self.channel and self.channel != record["id"]:
            raise WebDAVError("WebDAV 同步组记录缺失、损坏或已变化；不会创建替代库，请核对云端目录")
        return record["id"]

    def initialize(self):
        self.propfind(self.parts.path, depth=0)
        current = self.parts.path
        for part in self.folder.split("/") + ["expvault-webdav-v1"]:
            current += quote(part, safe="") + "/"
            self.collection(current)
        self.channel = self.channel_record(create=True)
        for name in ("objects", "commits", "heads"):
            self.collection(self.root + name + "/")
        return self.channel

    def info(self):
        self.channel_record()
        self.absent.clear()
        return {"hostname": "坚果云 WebDAV" if self.jianguoyun else "WebDAV 云端中转", "address": self.origin + self.root}

    def object_path(self, kind, key):
        if kind not in ("objects", "commits") or not isinstance(key, str) or not HEX.fullmatch(key):
            raise ValueError("同步对象编号无效")
        return self.root + kind + "/" + key[:2] + "/" + key

    def has(self, kind, key):
        path = self.object_path(kind, key)
        if path in self.present:
            return True
        if path in self.absent:
            return False
        code, _, _ = self.small("HEAD", path, accepted=(200, 404))
        (self.present if code == 200 else self.absent).add(path)
        return code == 200

    def move(self, source, destination, *, overwrite=False):
        code, _, _ = self.small("MOVE", source, headers={"Destination": self.origin + destination, "Overwrite": "T" if overwrite else "F"}, accepted=(201, 204, 412))
        if code == 412 and overwrite:
            raise WebDAVError("WebDAV 拒绝发布设备记录，将在下一轮重试")
        return code

    def receive(self, source, kind, key, stop=None, progress=None):
        if self.has(kind, key):
            return
        target = self.object_path(kind, key)
        self.collection(target.rsplit("/", 1)[0] + "/")
        temporary = target + "." + uuid.uuid4().hex + ".part"
        path = source.path(kind, key)
        if kind == "commits" and path.stat().st_size > MAX_COMMIT:
            raise WebDAVError("同步提交过大")
        if self.jianguoyun and path.stat().st_size > 500 * 1024**2:
            raise WebDAVError("文件超过坚果云默认 500 MB WebDAV 上传上限，请关闭视频同步或调整照片大小上限")
        with path.open("rb") as stream:
            self.small("PUT", temporary, stream=stream, size=path.stat().st_size,
                       headers={"If-None-Match": "*", "Content-Type": "application/octet-stream"}, accepted=(200, 201, 204), progress=progress, stop=stop)
        if self.move(temporary, target) == 412:
            # Another device won the immutable upload race. Remove only our own staging file.
            self.small("DELETE", temporary, accepted=(200, 204, 404))
        self.absent.discard(target)
        self.present.add(target)

    def send(self, destination, kind, key, stop=None, progress=None):
        if destination.has(kind, key):
            return
        connection, response = self.request("GET", self.object_path(kind, key), accepted=(200,))
        try:
            length = response.getheader("Content-Length")
            if length is not None and length.isdigit():
                size = int(length)
                destination.put_stream(kind, key, response, size, stop or self.stop, progress)
            else:
                # Some DAV servers use chunked downloads. Spool locally, never hold videos in RAM.
                with tempfile.TemporaryFile(dir=destination.root) as stream:
                    size, digest = 0, hashlib.sha256()
                    while block := response.read(CHUNK):
                        if self.stop.is_set() or stop and stop.is_set():
                            raise InterruptedError("同步已停止")
                        size += len(block)
                        if kind == "commits" and size > MAX_COMMIT:
                            raise WebDAVError("同步提交过大")
                        stream.write(block)
                        digest.update(block)
                        if progress:
                            progress(len(block))
                    if digest.hexdigest() != key:
                        raise WebDAVError("WebDAV 下载 SHA256 校验失败")
                    stream.seek(0)
                    destination.put_stream(kind, key, stream, size, stop or self.stop)
        finally:
            connection.close()

    def publish_head(self, head):
        if head == self.published:
            return
        if not HEX.fullmatch(head):
            raise ValueError("同步提交编号无效")
        target = self.root + "heads/" + self.device + ".json"
        temporary = target + "." + uuid.uuid4().hex + ".part"
        self.small("PUT", temporary, data=encoded({"protocol": 1, "head": head}), headers={"Content-Type": "application/json", "If-None-Match": "*"}, accepted=(200, 201, 204))
        self.move(temporary, target, overwrite=True)
        self.published = head

    def list_commits(self):
        pending = []
        prefix = unquote(self.root + "heads/")
        for response in self.propfind(self.root + "heads/"):
            href = urlsplit(response.findtext("{DAV:}href", ""))
            path = unquote(href.path)
            if path.rstrip("/") == prefix.rstrip("/"):
                continue
            if not path.startswith(prefix):
                raise WebDAVError("WebDAV 返回了同步目录以外的记录")
            name = path[len(prefix):]
            if not re.fullmatch(r"[a-f0-9]{32}\.json", name):
                continue
            good = [p for p in response.findall("{DAV:}propstat") if re.search(r"\s200\s", p.findtext("{DAV:}status", ""))]
            if not good:
                raise WebDAVError("WebDAV 无法读取设备记录")
            etag = good[0].findtext("{DAV:}prop/{DAV:}getetag")
            saved = self.head_cache.get(name)
            if saved and etag and saved[0] == etag:
                head = saved[1]
            else:
                _, data, _ = self.small("GET", self.root + "heads/" + name, maximum=4096)
                try:
                    value = json.loads(data)
                    head = value["head"]
                    if value.get("protocol") != 1 or not isinstance(head, str) or not HEX.fullmatch(head):
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    raise WebDAVError("WebDAV 设备记录损坏，已停止合并") from None
                self.head_cache[name] = (etag, head)
            pending.append(head)
        result = set()
        while pending:
            if self.stop.is_set():
                raise InterruptedError("同步已停止")
            key = pending.pop()
            if key in result:
                continue
            if len(result) >= 50000:
                raise WebDAVError("同步历史过长，请归档后建立新的同步目录")
            self.send(self.cache, "commits", key)
            try:
                value = json.loads(self.cache.get("commits", key))
                parents = value["parents"]
                if value.get("protocol") != 1 or not isinstance(parents, list) or not all(isinstance(p, str) and HEX.fullmatch(p) for p in parents):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise WebDAVError("WebDAV 同步历史损坏，已停止合并") from None
            result.add(key)
            pending.extend(parents)
        return sorted(result)
