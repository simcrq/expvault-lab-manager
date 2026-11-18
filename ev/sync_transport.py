"""Immutable content store, shared-folder relay and pinned HTTPS LAN relay."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, quote, unquote

from .sync_merge import encoded
from .sync_discovery import DiscoveryResponder, PAIRING_TTL, pairing_proof
from .util import now_iso

CHUNK = 1024 * 1024
HEX = re.compile(r"^[a-f0-9]{64}$")
MAX_COMMIT = 32 * 1024**2


def machine_name():
    return "".join(char for char in socket.gethostname() if char.isprintable())[:80]


def private_host(host):
    value = ipaddress.ip_address(host)
    if value.version != 4 or not value.is_private or value.is_unspecified or value.is_multicast or value.is_link_local:
        raise ValueError("直连同步只能使用明确的本机或私有 IPv4 地址")
    return host


class Repository:
    def __init__(self, root, *, create=False):
        self.root = Path(root).expanduser().absolute()
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError("同步目录不可用或使用了符号链接")
        for kind in ("objects", "commits"):
            path = self.root / kind
            if path.is_symlink():
                raise ValueError("同步存储不能使用符号链接")
            path.mkdir(exist_ok=True)

    def path(self, kind, key):
        if kind not in ("objects", "commits") or not HEX.fullmatch(key):
            raise ValueError("同步对象编号无效")
        path = self.root / kind / key
        if path.is_symlink() or path.parent.is_symlink() or self.root.is_symlink():
            raise ValueError("同步对象不能使用符号链接")
        return path

    def has(self, kind, key):
        return self.path(kind, key).is_file()

    def put_stream(self, kind, key, source, size, stop=None, progress=None):
        target = self.path(kind, key)
        if size < 0 or (kind == "commits" and size > MAX_COMMIT):
            raise ValueError("同步对象大小无效")
        if shutil.disk_usage(self.root).free < size + 32*1024**2:
            raise ValueError("同步目录磁盘空间不足")
        temporary = target.with_name("." + key + "-" + uuid.uuid4().hex + ".part")
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as stream:
                remaining = size
                while remaining:
                    if stop and stop.is_set():
                        raise InterruptedError("同步已停止")
                    block = source.read(min(CHUNK, remaining))
                    if not block:
                        raise ValueError("同步传输中断，完整对象尚未提交")
                    stream.write(block)
                    digest.update(block)
                    remaining -= len(block)
                    if progress:
                        progress(len(block))
                stream.flush()
                os.fsync(stream.fileno())
            if digest.hexdigest() != key:
                raise ValueError("同步对象 SHA256 校验失败")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def put_bytes(self, kind, data):
        import io
        key = hashlib.sha256(data).hexdigest()
        if not self.has(kind, key):
            self.put_stream(kind, key, io.BytesIO(data), len(data))
        return key

    def get(self, kind, key):
        path = self.path(kind, key)
        if kind == "commits" and path.stat().st_size > MAX_COMMIT:
            raise ValueError("同步提交过大")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != key:
            raise ValueError("同步提交内容损坏")
        return data

    def list_commits(self):
        return sorted(path.name for path in (self.root / "commits").iterdir() if HEX.fullmatch(path.name) and path.is_file())

    def send(self, destination, kind, key, stop=None, progress=None):
        if destination.has(kind, key):
            return
        path = self.path(kind, key)
        with path.open("rb") as stream:
            destination.put_stream(kind, key, stream, path.stat().st_size, stop, progress)

    def receive(self, source, kind, key, stop=None, progress=None):
        source.send(self, kind, key, stop, progress)


def pinned_connection(host, port, fingerprint):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(host, port, context=context, timeout=15)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, fingerprint):
            raise ValueError("同步证书指纹不匹配；未发送配对密钥或实验数据，请重新核对配对信息")
        return connection
    except Exception:
        connection.close()
        raise


def redeem_invite(peer, code):
    if (not isinstance(peer, dict) or not isinstance(code, str)
            or not all(isinstance(peer.get(key), str) for key in ("url", "fingerprint", "proof"))):
        raise ValueError("请选择在线主机并填写配对码")
    url, fingerprint, proof = (peer[key] for key in ("url", "fingerprint", "proof"))
    code = code.strip().replace("-", "").lower()
    if not re.fullmatch(r"[a-f0-9]{16}", code):
        raise ValueError("请输入接收电脑上显示的 16 位配对码")
    if not HEX.fullmatch(fingerprint) or not HEX.fullmatch(proof):
        raise ValueError("请在接收电脑上显示配对码，再重新搜索主机")
    if not hmac.compare_digest(pairing_proof(code, url, fingerprint), proof):
        raise ValueError("配对码与所选主机不匹配，请核对后重新搜索")
    parts = urlsplit(url)
    host = private_host(parts.hostname)
    port = parts.port
    if not port or url != f"https://{host}:{port}":
        raise ValueError("局域网主机地址无效")
    try:
        connection = pinned_connection(host, port, fingerprint)
        try:
            connection.request("POST", "/pair", headers={"Authorization": "Bearer " + code})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("配对码已失效，请在接收电脑重新生成并搜索")
            value = json.loads(response.read(2048))
        finally:
            connection.close()
    except (OSError, http.client.HTTPException):
        raise ValueError("无法连接所选主机，请确认局域网和防火墙设置后重试") from None
    if not isinstance(value, dict) or set(value) != {"url", "fingerprint", "token"} or value["url"] != url or value["fingerprint"] != fingerprint:
        raise ValueError("所选主机返回了无效的配对信息")
    return value


class HTTPSRepository:
    def __init__(self, url, token, fingerprint):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise ValueError("直连同步地址无效")
        private_host(parts.hostname)
        if not parts.port or not re.fullmatch(r"[a-zA-Z0-9_-]{40,100}", token) or not HEX.fullmatch(fingerprint):
            raise ValueError("配对信息无效")
        self.host, self.port = parts.hostname, parts.port
        self.token, self.fingerprint = token, fingerprint

    def connection(self):
        return pinned_connection(self.host, self.port, self.fingerprint)

    def request(self, method, path):
        connection = self.connection()
        try:
            connection.request(method, path, headers={"Authorization":"Bearer " + self.token, "X-Expvault-Device":quote(machine_name(),safe="")})
            response = connection.getresponse()
            return connection, response
        except Exception:
            connection.close()
            raise

    def has(self, kind, key):
        connection, response = self.request("HEAD", f"/{kind}/{key}")
        try:
            if response.status not in (200, 404):
                raise ValueError("无法访问同步服务，请核对密钥与服务状态")
            return response.status == 200
        finally:
            connection.close()

    def list_commits(self):
        connection, response = self.request("GET", "/commits")
        try:
            if response.status != 200 or int(response.getheader("Content-Length", "0")) > MAX_COMMIT:
                raise ValueError("无法读取远端同步提交")
            values = json.loads(response.read(MAX_COMMIT+1))
            if not isinstance(values, list) or not all(isinstance(key, str) and HEX.fullmatch(key) for key in values):
                raise ValueError("远端同步目录无效")
            return values
        finally:
            connection.close()

    def info(self):
        connection, response = self.request("GET", "/info")
        try:
            if response.status == 404:
                # v1.3.0 peers predate host discovery. An authenticated read still verifies reachability.
                self.list_commits()
                return {"hostname":"", "address":f"https://{self.host}:{self.port}"}
            if response.status != 200:
                raise ValueError("无法连接同步主机，请检查网络和配对信息")
            value = json.loads(response.read(4097))
            if not isinstance(value,dict):
                raise ValueError("同步主机信息无效")
            name = value.get("hostname", "")
            if not isinstance(name,str) or len(name) > 80 or any(not char.isprintable() for char in name):
                raise ValueError("同步主机信息无效")
            return {"hostname":name,"address":f"https://{self.host}:{self.port}"}
        finally:
            connection.close()

    def send(self, destination, kind, key, stop=None, progress=None):
        if destination.has(kind, key):
            return
        connection, response = self.request("GET", f"/{kind}/{key}")
        try:
            if response.status != 200:
                raise ValueError("远端对象尚未完整到达，将自动重试")
            destination.put_stream(kind, key, response, int(response.getheader("Content-Length")), stop, progress)
        finally:
            connection.close()

    def receive(self, source, kind, key, stop=None, progress=None):
        if self.has(kind, key):
            return
        path = source.path(kind, key)
        connection = self.connection()
        try:
            connection.putrequest("PUT", f"/{kind}/{key}")
            connection.putheader("Authorization", "Bearer " + self.token)
            connection.putheader("Content-Length", str(path.stat().st_size))
            connection.putheader("X-Expvault-Device", quote(machine_name(),safe=""))
            connection.endheaders()
            with path.open("rb") as stream:
                while block := stream.read(CHUNK):
                    if stop and stop.is_set():
                        raise InterruptedError("同步已停止")
                    connection.send(block)
                    if progress:
                        progress(len(block))
            response = connection.getresponse()
            response.read()
            if response.status != 201:
                raise ValueError("同步对象未被远端接受")
        finally:
            connection.close()


class LANServer:
    def __init__(self, repository, state, host, *, port=0, token=None, on_peer=None, on_received=None):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        private_host(host)
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True)
        cert_path, key_path = state / "sync-cert.pem", state / "sync-key.pem"
        if cert_path.is_symlink() or key_path.is_symlink():
            raise ValueError("同步证书路径无效")
        if not cert_path.exists() or not key_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expvault-private-sync")])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=3650))
                    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), critical=False)
                    .sign(key, hashes.SHA256()))
            temporary_key = key_path.with_name(".key-"+uuid.uuid4().hex+".tmp")
            with os.fdopen(os.open(temporary_key, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600),"wb") as stream:
                stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            os.replace(temporary_key,key_path)
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        self.fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        self.token = token or secrets.token_urlsafe(32)
        self.repository = repository
        self.hostname = machine_name()
        self.invite_lock = threading.Lock()
        self.invite = None
        self.peers = {}
        self.peer_lock = threading.Lock()
        self.on_peer = on_peer
        self.on_received = on_received
        self.receiving = {}
        self.incoming = {}
        owner = self
        self.slots = threading.BoundedSemaphore(4)

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(15)
                self.request = context.wrap_socket(self.request, server_side=True)
                super().setup()

            def finish(self):
                try:
                    super().finish()
                finally:
                    self.connection.close()

            def handle(self):
                try:
                    super().handle()
                except (OSError, ConnectionError):
                    # A peer can disconnect after checking the TLS pin, before HTTP starts.
                    pass

            def log_message(self, *_args):
                pass

            def respond(self, code, body=b""):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def handle_request(self):
                self.connection.settimeout(15)
                if not hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"), ("Bearer " + owner.token).encode()):
                    self.respond(401)
                    return
                owner.touch_peer(self.client_address[0],self.headers.get("X-Expvault-Device", ""))
                if not owner.slots.acquire(blocking=False):
                    self.respond(429)
                    return
                try:
                    if self.path == "/info" and self.command == "GET":
                        self.respond(200, encoded({"hostname":owner.hostname}))
                        return
                    if self.path == "/commits" and self.command == "GET":
                        self.respond(200, encoded(owner.repository.list_commits()))
                        return
                    match = re.fullmatch(r"/(objects|commits)/([a-f0-9]{64})", self.path)
                    if not match:
                        self.respond(404)
                        return
                    kind, key = match.groups()
                    path = owner.repository.path(kind, key)
                    if self.command == "PUT":
                        if self.headers.get("Transfer-Encoding") or not self.headers.get("Content-Length", "").isdigit():
                            self.respond(400)
                            return
                        size = int(self.headers["Content-Length"])
                        transfer = uuid.uuid4().hex if kind == "objects" else None
                        if transfer:
                            owner.begin_receive(transfer,size,self.client_address[0])
                        accepted = False
                        try:
                            owner.repository.put_stream(kind, key, self.rfile, size,
                                progress=(lambda count:owner.receive_bytes(count)) if transfer else None)
                            accepted = True
                        finally:
                            if transfer:
                                owner.end_receive(transfer,size,self.client_address[0],accepted)
                        self.respond(201)
                    elif path.is_file():
                        self.send_response(200)
                        self.send_header("Content-Length", str(path.stat().st_size))
                        self.end_headers()
                        if self.command == "GET":
                            with path.open("rb") as stream:
                                shutil.copyfileobj(stream, self.wfile, CHUNK)
                    else:
                        self.respond(404)
                except (ValueError, OSError):
                    self.respond(400)
                finally:
                    owner.slots.release()

            do_GET = do_HEAD = do_PUT = handle_request

            def do_POST(self):
                if self.path != "/pair":
                    self.respond(404)
                    return
                with owner.invite_lock:
                    code = owner.invite_code()
                    supplied = self.headers.get("Authorization", "").encode()
                    if not code or not hmac.compare_digest(supplied, ("Bearer " + code).encode()):
                        self.respond(403)
                        return
                    self.respond(200, encoded(owner.pairing()))
                    owner.invite = None

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
        self.host, self.port = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval":.1}, daemon=True)
        self.thread.start()
        try:
            self.discovery = DiscoveryResponder(host, self.discovery_info)
        except OSError:
            self.discovery = None

    def pairing(self):
        return {"url":f"https://{self.host}:{self.port}", "token":self.token, "fingerprint":self.fingerprint}

    def invite_code(self):
        if self.invite and self.invite[1] > time.monotonic():
            return self.invite[0]
        return ""

    def new_invite(self):
        with self.invite_lock:
            if not self.discovery:
                return {"code": ""}
            code = secrets.token_hex(8)
            self.invite = code, time.monotonic() + PAIRING_TTL
        return {"code": "-".join(code[i:i+4] for i in range(0, len(code), 4)), "expires_in": PAIRING_TTL}

    def discovery_info(self):
        with self.invite_lock:
            code = self.invite_code()
        url = self.pairing()["url"]
        return {"hostname": self.hostname, "port": self.port, "fingerprint": self.fingerprint,
                "proof": pairing_proof(code, url, self.fingerprint) if code else ""}

    def touch_peer(self, address, name):
        name = unquote(name[:320])
        name = "".join(char for char in name if char.isprintable())[:80]
        now = time.time()
        with self.peer_lock:
            previous = self.peers.get(address)
            self.peers[address] = {"hostname":name or address,"address":address,"last_seen":now_iso(),"seen_at":now}
            if len(self.peers) > 20:
                oldest = min(self.peers,key=lambda key:self.peers[key]["seen_at"])
                del self.peers[oldest]
        if self.on_peer and (not previous or now-previous["seen_at"] > 90):
            self.on_peer(name or address,address)

    def connections(self):
        with self.peer_lock:
            return [{key:value for key,value in peer.items() if key != "seen_at"} | {"recent":time.time()-peer["seen_at"] <= 90}
                    for peer in sorted(self.peers.values(),key=lambda peer:peer["seen_at"],reverse=True)]

    def begin_receive(self, transfer, size, address):
        with self.peer_lock:
            if not self.receiving:
                self.incoming = {"phase":"receiving","label":"接收远端文件（随后自动合并）","bytes_total":0,
                    "bytes_done":0,"total_files":0,"done_files":0,"failed":False}
            self.receiving[transfer] = address
            self.incoming["bytes_total"] += size
            self.incoming["total_files"] += 1
            self.incoming["current_file"] = self.peers.get(address,{}).get("hostname",address)+" · "+address

    def receive_bytes(self, count):
        with self.peer_lock:
            self.incoming["bytes_done"] += count

    def end_receive(self, transfer, size, address, accepted):
        with self.peer_lock:
            self.receiving.pop(transfer,None)
            self.incoming["done_files"] += int(accepted)
            self.incoming["failed"] |= not accepted
            self.incoming["finished_at"] = time.time()
        if self.on_received:
            self.on_received(address,size,accepted)

    def incoming_progress(self):
        with self.peer_lock:
            if not self.incoming:
                return None
            value = dict(self.incoming)
            active = bool(self.receiving)
            value.update(active=active,percent=min(100,int(value["bytes_done"]*100/max(1,value["bytes_total"]))),indeterminate=False)
            if not active:
                value.update(phase="waiting",label="远端传输中断，等待对端重试" if value["failed"] else "已接收远端文件，等待下一轮合并")
                if not value["failed"]:
                    value["percent"] = 100
            return value

    def close(self):
        with self.invite_lock:
            self.invite = None
            discovery, self.discovery = self.discovery, None
        if discovery:
            discovery.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
