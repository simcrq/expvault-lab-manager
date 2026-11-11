"""本机 / 可信局域网实验管理 HTTP 服务，不需要 Qt 或前端构建。"""
from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import threading
import time
import uuid
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlsplit

from filelock import Timeout

from . import config
from .file_tags import normalize_tags
from .importer import portable_name
from .util import CHUNK, now_iso, write_json
from .web_service import WebError, WebService
from .web_pairing import WebPairing
from .workspaces import WorkspaceRegistry, WorkspaceHub, DEFAULT

ASSETS = Path(__file__).with_name("web_assets")
MAX_JSON = 2 * 1024 * 1024
SAFE_INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".mp4", ".mov", ".m4v", ".webm", ".ogg", ".mp3", ".wav", ".pdf"}


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("重复 JSON 字段")
            value[key] = item
        return value
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("无效 JSON 数值")))


def byte_range(value, size):
    if not value:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or size == 0 or not any(match.groups()):
        raise WebError("无效的文件范围", status=416, data={"size": size})
    first, last = match.groups()
    if not first:
        length = int(last)
        if length <= 0:
            raise WebError("无效的文件范围", status=416, data={"size": size})
        return max(0, size-length), size-1, True
    start, end = int(first), min(int(last), size-1) if last else size-1
    if start >= size or start > end:
        raise WebError("文件范围超出文件长度", status=416, data={"size": size})
    return start, end, True


class WebServer:
    def __init__(self, root, *, host="127.0.0.1", port=8765, ttl=12*3600, hub=None, workspace_id=None):
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("请使用明确的本机或局域网 IPv4 地址") from None
        if ip.version != 4 or not ip.is_private or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError("网页服务只能绑定明确的本机或局域网 IPv4 地址，不能使用 0.0.0.0 或公网地址")
        self.owns_hub = hub is None
        if hub is None:
            registry, workspace_id = WorkspaceRegistry.locate(root)
            hub = WorkspaceHub(registry, host=host, ttl=ttl)
        if hub.registry.location(workspace_id) != Path(root).expanduser().resolve():
            raise ValueError("工作空间目录与编号不匹配")
        self.hub, self.workspace_id = hub, workspace_id
        workspace = hub.registry.get(workspace_id)
        self.service = WebService(root)
        self.service.sync_root = hub.registry.root
        self.pairing = WebPairing(self.service, workspace_name=workspace["name"])
        self.token = secrets.token_urlsafe(32)
        self.expires_at = hub.expires_at
        self.stopped = threading.Event()
        self.connections_lock = threading.Lock()
        self.connections = set()
        self.upload_slots = threading.BoundedSemaphore(2)
        self.thread = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "expvault"

            def setup(self):
                super().setup()
                self.connection.settimeout(30)
                with owner.connections_lock:
                    owner.connections.add(self.connection)

            def finish(self):
                try:
                    super().finish()
                finally:
                    with owner.connections_lock:
                        owner.connections.discard(self.connection)

            def log_message(self, *_args):
                pass

            def headers_common(self):
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

            def respond(self, status, value, *, cookie=False):
                body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(status)
                self.headers_common()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if cookie:
                    self.send_header("Set-Cookie", f"{owner.cookie_name}={owner.token}; HttpOnly; SameSite=Strict; Path=/")
                if status == 416:
                    self.send_header("Content-Range", f"bytes */{value.get('data', {}).get('size', 0)}")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def boundary(self):
                if self.headers.get("Host", "").lower() not in owner.allowed_hosts:
                    raise WebError("访问地址无效，请使用启动时提供的链接", status=403)
                origin = self.headers.get("Origin")
                if origin and origin.lower() not in owner.allowed_origins:
                    raise WebError("不允许跨站访问", status=403)

            def authorize(self, *, mutation=False):
                self.boundary()
                authorization = self.headers.get("Authorization", "")
                supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
                if not supplied:
                    cookie = SimpleCookie()
                    try:
                        cookie.load(self.headers.get("Cookie", ""))
                        supplied = cookie[owner.cookie_name].value if owner.cookie_name in cookie else ""
                    except Exception:
                        supplied = ""
                if not hmac.compare_digest(supplied.encode("utf-8"), owner.token.encode("utf-8")):
                    raise WebError("登录链接无效，请用电脑端提供的完整链接打开", status=401, code="UNAUTHORIZED")
                if owner.stopped.is_set() or time.monotonic() >= owner.expires_at:
                    raise WebError("网页会话已过期，请重新启动服务取得新链接", status=401, code="SESSION_EXPIRED")
                if mutation and self.headers.get("X-Expvault-Request") != "1":
                    raise WebError("写入请求校验失败", status=403)

            def query(self, url):
                values = parse_qs(url.query, keep_blank_values=True, max_num_fields=20)
                if any(len(value) != 1 for value in values.values()):
                    raise WebError("请求参数重复")
                return {key: value[0] for key, value in values.items()}

            def local_controls(self):
                return ipaddress.ip_address(self.client_address[0]).is_loopback

            def workspace_action(self, action):
                try:
                    return action()
                except ValueError as exc:
                    raise WebError(str(exc)) from None

            def body_json(self):
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise WebError("接口仅接受 JSON 请求", status=415)
                length = self.length(MAX_JSON)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise WebError("请求未传输完整")
                value = strict_json(raw)
                if not isinstance(value, dict):
                    raise WebError("请求必须是 JSON 对象")
                return value

            def length(self, maximum):
                value = self.headers.get("Content-Length", "")
                if not value.isdecimal() or len(value) > 20 or self.headers.get("Transfer-Encoding"):
                    raise WebError("请求必须提供有效长度", status=411)
                size = int(value)
                if size > maximum:
                    raise WebError("文件或请求超过接收上限", status=413)
                return size

            def serve_file(self, path, *, download=False):
                with path.open("rb") as stream:
                    size = os.fstat(stream.fileno()).st_size
                    start, end, partial = byte_range(self.headers.get("Range", ""), size)
                    length = max(0, end-start+1)
                    self.send_response(206 if partial else 200)
                    self.headers_common()
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                    inline = not download and path.suffix.lower() in SAFE_INLINE
                    self.send_header("Content-Disposition", ("inline" if inline else "attachment") + "; filename*=UTF-8''" + quote(path.name, safe=""))
                    if partial:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.end_headers()
                    if self.command == "HEAD":
                        return
                    stream.seek(start)
                    remaining = length
                    while remaining and not owner.stopped.is_set():
                        block = stream.read(min(CHUNK, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)

            def get(self):
                self.boundary()
                url = urlsplit(self.path)
                assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
                          "/styles.css": ("styles.css", "text/css; charset=utf-8"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
                if url.path in assets:
                    name, mime = assets[url.path]
                    body = (ASSETS / name).read_bytes()
                    self.send_response(200)
                    self.headers_common()
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                    return
                self.authorize()
                query = self.query(url)
                if url.path == "/api/file":
                    with owner.service.context() as ctx:
                        rec = owner.service.record(ctx, query.get("exp_id", ""))
                        path = owner.service.file_path(ctx, rec, query.get("path", ""))
                    self.serve_file(path, download=query.get("download") == "1")
                elif url.path == "/api/report":
                    self.serve_file(owner.service.export_path(query.get("id", "")), download=True)
                elif url.path.startswith("/api/jobs/"):
                    self.respond(200, owner.service.job(url.path.removeprefix("/api/jobs/")))
                elif url.path == "/api/jobs":
                    self.respond(200, {"items": owner.service.running_jobs()})
                elif url.path == "/api/workspaces":
                    self.respond(200, {**self.workspace_action(owner.hub.registry.list), "current": owner.workspace_id})
                elif url.path == "/api/sync":
                    if not self.local_controls():
                        raise WebError("请在本机浏览器查看或配置同步", status=403)
                    self.respond(200, owner.hub.sync.public())
                elif url.path == "/api/sync/logs":
                    if not self.local_controls():
                        raise WebError("请在本机浏览器查看同步日志", status=403)
                    try:
                        limit = int(query.get("limit","100"))
                    except ValueError:
                        raise WebError("日志数量无效")
                    self.respond(200,self.workspace_action(lambda:owner.hub.sync.activity.logs(limit)))
                elif url.path == "/api/connectivity":
                    self.respond(200, owner.pairing.connectivity(local=self.local_controls()))
                elif url.path == "/api/pairings":
                    self.respond(200, owner.pairing.info(query.get("id", "")))
                elif url.path.startswith("/api/"):
                    self.respond(200, owner.service.read(url.path[5:], query))
                else:
                    raise WebError("页面不存在", status=404)

            def upload(self, query):
                exp_id = query.get("exp_id", "")
                with owner.service.context() as ctx:
                    owner.service.record(ctx, exp_id)
                size = self.length(20*1024**3)
                name = unquote(self.headers.get("X-File-Name", ""), errors="strict")
                if not name or name in (".", "..") or "/" in name or "\\" in name or len(name) > 1000:
                    raise WebError("文件名无效")
                relative = unquote(self.headers.get("X-File-Relative", ""), errors="strict") or name
                relative_path = PurePosixPath(relative.replace("\\", "/"))
                if relative_path.is_absolute() or ".." in relative_path.parts or len(relative) > 4096:
                    raise WebError("文件夹相对路径无效")
                tags = normalize_tags(unquote(self.headers.get("X-File-Tags", ""), errors="strict"))
                if not owner.upload_slots.acquire(blocking=False):
                    raise WebError("另有文件正在传输，请稍后重试", status=429)
                directory, complete = None, False
                try:
                    if shutil.disk_usage(owner.service.inbox).free < size + 64*1024**2:
                        raise WebError("电脑磁盘空间不足", status=507)
                    upload_id = uuid.uuid4().hex
                    directory = owner.service.inbox / upload_id
                    directory.mkdir()
                    path = directory / portable_name(name)
                    temporary = directory / ".upload.part"
                    with temporary.open("xb") as stream:
                        remaining = size
                        while remaining:
                            if owner.stopped.is_set():
                                raise ConnectionAbortedError("服务已停止")
                            block = self.rfile.read(min(CHUNK, remaining))
                            if not block:
                                raise ConnectionAbortedError("上传中断")
                            stream.write(block)
                            remaining -= len(block)
                        stream.flush()
                        os.fsync(stream.fileno())
                    write_json(directory / ".upload.json", {"exp_id": exp_id, "name": path.name, "relative": relative,
                                                             "tags": tags, "created_at": now_iso()})
                    os.replace(temporary, path)
                    complete = True
                    self.respond(201, {"id": upload_id, "name": path.name, "size": size, "tags": tags})
                finally:
                    if directory is not None and not complete:
                        shutil.rmtree(directory, ignore_errors=True)
                    owner.upload_slots.release()

            def post(self):
                self.authorize(mutation=True)
                url = urlsplit(self.path)
                if url.path == "/api/uploads":
                    self.upload(self.query(url))
                    return
                value = self.body_json()
                if url.path == "/api/session":
                    session = owner.service.read("session", {})
                    session["workspace"] = self.workspace_action(lambda: owner.hub.registry.get(owner.workspace_id))
                    self.respond(200, session, cookie=True)
                elif url.path == "/api/workspaces":
                    if not {"name"} <= set(value) <= {"name", "operator", "project"}:
                        raise WebError("工作空间创建字段无效")
                    self.respond(201, self.workspace_action(lambda: owner.hub.create(**value)))
                elif url.path == "/api/workspaces/select":
                    if set(value) != {"id"} or not isinstance(value["id"], str):
                        raise WebError("工作空间选择字段无效")
                    self.respond(200, self.workspace_action(lambda: owner.hub.select(value["id"])))
                elif url.path in ("/api/sync/configure", "/api/sync/action"):
                    if not self.local_controls():
                        raise WebError("请在本机浏览器控制同步", status=403)
                    if url.path.endswith("configure"):
                        result = self.workspace_action(lambda: owner.hub.sync.configure(value))
                    else:
                        if "action" not in value or not isinstance(value["action"], str):
                            raise WebError("同步操作字段无效")
                        action = value.pop("action")
                        result = self.workspace_action(lambda: owner.hub.sync.action(action, **value))
                    self.respond(200, result)
                elif url.path == "/api/connectivity/hotspot":
                    if not self.local_controls():
                        raise WebError("请在这台电脑的本机浏览器中控制 Windows 热点", status=403)
                    if set(value) != {"action"} or not isinstance(value["action"], str):
                        raise WebError("热点操作字段无效")
                    self.respond(200, owner.pairing.change_hotspot(value["action"]))
                elif url.path == "/api/pairings":
                    if set(value) != {"exp_id", "host"} or not all(isinstance(item, str) for item in value.values()):
                        raise WebError("手机接收字段无效")
                    self.respond(201, owner.pairing.create(value["exp_id"], value["host"]))
                elif url.path == "/api/pairings/stop":
                    if set(value) != {"id"} or not isinstance(value["id"], str):
                        raise WebError("手机接收会话字段无效")
                    self.respond(200, owner.pairing.stop(value["id"]))
                elif url.path == "/api/actions":
                    if set(value) != {"request_id", "operation", "arguments"}:
                        raise WebError("请求字段无效")
                    self.respond(202, owner.service.submit(value["request_id"], value["operation"], value["arguments"]))
                elif url.path.startswith("/api/jobs/") and url.path.endswith("/cancel"):
                    self.respond(200, owner.service.cancel(url.path[10:-7]))
                else:
                    raise WebError("接口不存在", status=404)

            def dispatch(self, function):
                try:
                    function()
                except WebError as exc:
                    self.respond(exc.status, {"error": str(exc), "code": exc.code, "data": exc.data})
                except (ValueError, UnicodeError, json.JSONDecodeError):
                    self.respond(400, {"error": "请求格式无效", "code": "INVALID_ARGUMENTS"})
                except Timeout:
                    self.respond(409, {"error": "另一个请求正在处理，请稍后重试", "code": "BUSY"})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
                    pass
                except Exception:
                    self.respond(500, {"error": "存储或服务操作失败，请检查磁盘、权限与实际数据", "code": "IO_ERROR"})

            def do_GET(self):
                self.dispatch(self.get)

            def do_HEAD(self):
                self.dispatch(self.get)

            def do_POST(self):
                self.dispatch(self.post)

        try:
            self.server = ThreadingHTTPServer((host, port), Handler)
        except OSError:
            self.service.close()
            raise
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.host, self.port = self.server.server_address
        self.cookie_name = f"ev_session_{self.port}"
        self.allowed_hosts = {f"{self.host}:{self.port}"}
        if ip.is_loopback:
            self.allowed_hosts.add(f"localhost:{self.port}")
        self.allowed_origins = {"http://" + host for host in self.allowed_hosts}
        try:
            self.hub.register(self.workspace_id, self)
        except Exception:
            self.server.server_close()
            self.service.close()
            raise

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/#{self.token}"

    def start(self):
        with self.hub.lock:
            if self.stopped.is_set() or self.hub.closed:
                raise ValueError("网页服务已关闭，请重新启动")
            if self.thread is None:
                self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
                self.thread.start()
                self.hub.sync.start()
        return self

    def close(self, *, _hub_closing=False):
        if self.owns_hub and not _hub_closing:
            self.hub.close()
            return
        if self.stopped.is_set():
            return
        self.stopped.set()
        with self.connections_lock:
            for connection in list(self.connections):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=2)
        self.server.server_close()
        self.pairing.close()
        # 已开始的写操作完成或取消后再退出，避免丢失已确认的实验数据。
        self.service.close()
        with self.hub.lock:
            if self.hub.servers.get(self.workspace_id) is self:
                self.hub.servers.pop(self.workspace_id)


def run(root, *, host="127.0.0.1", port=8765, open_browser=True):
    server = WebServer(root, host=host, port=port).start()
    try:
        entry_url = server.url
        if server.workspace_id == DEFAULT:
            selected = server.hub.registry.list()["selected"]
            try:
                entry_url = server.hub.open(selected).url
            except (ValueError, WebError, OSError) as exc:
                print(f"上次的工作空间暂不可用：{exc}。已打开当前库，可在工作空间列表中重新选择。")
        print("expvault 网页管理已启动：\n" + entry_url)
        print("关闭此终端或按 Ctrl+C 停止全部工作空间服务。管理会话可切换已登记的工作空间，请仅向可信设备提供。")
        if not ipaddress.ip_address(host).is_loopback:
            print("校园网可能隔离设备；手机可使用独立热点或 USB 共享网络连接此地址。")
        if open_browser:
            webbrowser.open(entry_url)
        while not server.stopped.wait(.5):
            pass
    except KeyboardInterrupt:
        print("\n正在停止服务，等待已开始的操作结束…")
    finally:
        server.close()
    return 0
