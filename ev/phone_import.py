"""临时局域网手机上传端；只接收文件到 inbox，不直接写实验目录。"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .file_tags import normalize_tags
from .importer import ImportItem, file_kind, portable_name
from .util import CHUNK

MOBILE_PAGE = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>expvault · 手机传入</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f7f6;color:#17302c;font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:660px;margin:auto;padding:28px 20px}small{color:#58756d}h1{font-size:30px;margin:10px 0}p{line-height:1.7}
.card{background:white;border:1px solid #d8e4de;border-radius:16px;padding:20px;margin:20px 0}
button,.pick{display:block;background:#18705c;color:white;border:0;border-radius:10px;padding:15px;font:inherit;text-align:center;cursor:pointer;width:100%;margin:12px 0}
button:disabled{opacity:.5}input[type=text]{width:100%;padding:12px;border:1px solid #c7d8d0;border-radius:8px;font:inherit;margin:8px 0}
.file{padding:12px 0;border-top:1px solid #e1e9e5;overflow-wrap:anywhere}.state{font-size:14px;color:#58756d}
progress{width:100%;accent-color:#18705c}#notice{white-space:pre-wrap;color:#8d4324}input[type=file]{position:absolute;opacity:0;width:1px;height:1px}
</style>
<main><small>EXPVAULT / 实验资料传入</small><h1>把现场记录带回来</h1>
<p id="workspace" hidden></p><p id="target">正在连接电脑…</p><div class="card">
<p>选择照片、视频或数据文件，发送后在电脑端核对并导入。</p>
<p><strong>请发送原图、原视频。</strong>建议从手机文件中选择原始文件，保留原格式；避免发送截图、聊天压缩图或压缩后的视频。</p>
<label class="pick" for="files" tabindex="0">选择照片、视频或文件</label>
<input id="files" type="file" multiple>
<label for="batch">批量标签（逗号分隔，可含空格）</label><input id="batch" type="text" placeholder="样品 A, 退火前, 显微镜">
<button id="apply" type="button">将标签追加到全部文件</button><div id="list"></div>
<button id="send" disabled>发送到电脑</button><progress id="progress" max="100" value="0"></progress>
<p id="notice" role="status"></p></div>
<small>请先连接电脑热点，或确保手机与电脑能在局域网直接互通；同一校园网可能有终端隔离。传输使用局域网 HTTP，请在可信网络使用；离开此页或锁屏可能中断上传。发送成功不等于已导入，请在电脑端完成确认。</small></main>
<script>
const token=location.hash.slice(1)||sessionStorage.getItem('expvault.phone.token')||'';if(token)sessionStorage.setItem('expvault.phone.token',token);history.replaceState(null,'',location.pathname);
const $=id=>document.getElementById(id);let rows=[],busy=false;
const tags=s=>[...new Set(s.split(/[,，;；\n]+/).map(x=>x.trim()).filter(Boolean))];
async function connect(){try{const r=await fetch('/session',{headers:{Authorization:'Bearer '+token}});const d=await r.json();if(!r.ok)throw Error(d.error);if(d.workspace){$('workspace').textContent='工作空间：'+d.workspace;$('workspace').hidden=false;}$('target').textContent='目标实验：'+d.experiment;$('send').disabled=false;}catch(e){$('target').textContent='连接失败';$('notice').textContent=e.message+'。请在电脑端重新开启手机传入并扫码。';}}
$('files').onchange=()=>{if(busy)return;for(const f of $('files').files){const el=document.createElement('div');el.className='file';const name=document.createElement('div');name.textContent=f.name+' · '+(f.size/1048576).toFixed(1)+' MB';const input=document.createElement('input');input.type='text';input.placeholder='此文件的标签，逗号分隔';input.value=$('batch').value;const state=document.createElement('div');state.className='state';state.textContent='待发送';el.append(name,input,state);$('list').append(el);rows.push({f,input,state,sent:false});}$('files').value='';};
$('apply').onclick=()=>{if(busy)return;for(const row of rows)if(!row.sent)row.input.value=[...new Set([...tags(row.input.value),...tags($('batch').value)])].join(', ');};
function upload(row,done,total){return new Promise((resolve,reject)=>{const x=new XMLHttpRequest();x.open('POST','/upload');x.timeout=60*60*1000;x.setRequestHeader('Authorization','Bearer '+token);x.setRequestHeader('X-File-Name',encodeURIComponent(row.f.name));x.setRequestHeader('X-File-Tags',encodeURIComponent(row.input.value));x.upload.onprogress=e=>{if(e.lengthComputable){$('progress').value=(done+e.loaded)/total*100;row.state.textContent='上传中 '+Math.round(e.loaded/Math.max(e.total,1)*100)+'%';}};x.onload=()=>{let d;try{d=JSON.parse(x.responseText);}catch(e){reject(Error('电脑返回了无效响应'));return;}if(x.status!==201){reject(Error(d.error||'上传失败'));return;}row.sent=true;row.input.disabled=true;row.state.textContent='已传到电脑 · 等待桌面端导入';resolve();};x.onerror=()=>reject(Error('连接中断，请保持屏幕点亮后重试'));x.ontimeout=()=>reject(Error('传输超时，请重试'));x.send(row.f);});}
$('send').onclick=async()=>{if(busy)return;const pending=rows.filter(r=>!r.sent);if(!pending.length){$('notice').textContent='请先选择文件';return;}busy=true;$('send').disabled=true;$('apply').disabled=true;$('files').disabled=true;rows.forEach(r=>r.input.disabled=true);const total=pending.reduce((s,r)=>s+r.f.size,0)||1;let done=0,errors=0;for(const row of pending){try{await upload(row,done,total);}catch(e){row.state.textContent='失败：'+e.message;errors++;}done+=row.f.size;}$('progress').value=100;$('notice').textContent=errors?'部分文件未发送，点击发送可重试失败的文件。':'发送完成，请在电脑端检查文件、标签并确认导入。';busy=false;$('send').disabled=false;$('apply').disabled=false;$('files').disabled=false;rows.forEach(r=>r.input.disabled=r.sent);};
connect();
</script></html>'''


class PhoneReceiver:
    def __init__(self, inbox: Path, experiment: str, *, host="127.0.0.1", port=0,
                 on_received=None, max_file_bytes=20 * 1024**3, ttl=7200, exp_id="", workspace_name=""):
        if not ipaddress.ip_address(host).is_private:
            raise ValueError("手机导入只能绑定本机或局域网地址")
        self.inbox = Path(inbox)
        self.experiment = experiment
        self.exp_id = exp_id
        self.workspace_name = workspace_name
        self.token = secrets.token_urlsafe(32)
        self.on_received = on_received
        self.max_file_bytes = max_file_bytes
        self.expires_at = time.monotonic() + ttl
        self.stopped = threading.Event()
        self.upload_lock = threading.Lock()
        self.connections_lock = threading.Lock()
        self.connections = set()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(30)
                with receiver.connections_lock:
                    receiver.connections.add(self.connection)

            def finish(self):
                try:
                    super().finish()
                finally:
                    with receiver.connections_lock:
                        receiver.connections.discard(self.connection)

            def log_message(self, *_args):
                pass  # 不记录令牌、文件名或个人数据。

            def respond(self, code, data, *, html=False):
                body = data.encode("utf-8") if html else json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied.encode(), ("Bearer " + receiver.token).encode()):
                    self.respond(403, {"error": "配对链接无效"})
                    return False
                if receiver.stopped.is_set() or time.monotonic() >= receiver.expires_at:
                    self.respond(410, {"error": "手机传入已关闭或配对已过期"})
                    return False
                # 浏览器同源检查；不开放 CORS，远程网页不能借用配对会话。
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + self.headers.get("Host", ""):
                    self.respond(403, {"error": "不允许跨站上传"})
                    return False
                return True

            def do_GET(self):
                if self.path == "/":
                    self.respond(200, MOBILE_PAGE, html=True)
                elif self.path == "/session":
                    if self.authorized():
                        self.respond(200, {"experiment": receiver.experiment, "workspace": receiver.workspace_name,
                                           "max_file_bytes": receiver.max_file_bytes})
                else:
                    self.respond(404, {"error": "页面不存在"})

            def do_POST(self):
                if self.path != "/upload":
                    self.respond(404, {"error": "接口不存在"})
                    return
                if not self.authorized():
                    return
                if not receiver.upload_lock.acquire(blocking=False):
                    self.respond(409, {"error": "另一文件正在上传，请稍后重试"})
                    return
                partial = None
                try:
                    raw_length = self.headers.get("Content-Length", "")
                    if not raw_length.isdecimal() or self.headers.get("Transfer-Encoding"):
                        self.respond(411, {"error": "上传必须提供文件长度"})
                        return
                    length = int(raw_length)
                    if length > receiver.max_file_bytes:
                        self.respond(413, {"error": "单个文件超过接收上限（默认 20 GB），请通过 U 盘导入"})
                        return
                    raw_name = self.headers.get("X-File-Name", "")
                    name = unquote(raw_name, errors="strict")
                    if not name or name in (".", "..") or "/" in name or "\\" in name or len(name) > 1000:
                        self.respond(400, {"error": "文件名无效"})
                        return
                    tags = normalize_tags(unquote(self.headers.get("X-File-Tags", ""), errors="strict"))
                    receiver.inbox.mkdir(parents=True, exist_ok=True)
                    if shutil.disk_usage(receiver.inbox).free < length + 64 * 1024**2:
                        self.respond(507, {"error": "电脑磁盘空间不足"})
                        return
                    directory = receiver.inbox / uuid.uuid4().hex
                    directory.mkdir()
                    destination = directory / portable_name(name)
                    partial = directory / ".upload.part"
                    with partial.open("xb") as stream:
                        remaining = length
                        while remaining:
                            if receiver.stopped.is_set():
                                raise ConnectionAbortedError("接收已关闭")
                            block = self.rfile.read(min(CHUNK, remaining))
                            if not block:
                                raise ConnectionAbortedError("上传中断")
                            stream.write(block)
                            remaining -= len(block)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # 伴随文件保证窗口关闭/重启后仍保留手机端设置的标签。
                    info = directory / ".upload.json"
                    manifest = {"name": destination.name, "tags": tags}
                    if receiver.exp_id:
                        manifest.update(exp_id=receiver.exp_id, relative=destination.name)
                    info.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                    os.replace(partial, destination)
                    partial = None
                    item = ImportItem(destination, destination.name, length, file_kind(destination), tags=tags, origin="手机上传")
                    if receiver.on_received and not receiver.stopped.is_set():
                        receiver.on_received(item)
                    self.respond(201, {"ok": True, "name": destination.name})
                except (ValueError, UnicodeError):
                    self.respond(400, {"error": "文件名或标签格式无效"})
                except (OSError, TimeoutError):
                    try:
                        self.respond(500, {"error": "传输未完成，请检查网络和电脑磁盘后重试"})
                    except OSError:
                        pass
                finally:
                    if partial is not None:
                        partial.unlink(missing_ok=True)
                    receiver.upload_lock.release()

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.host, self.port = self.server.server_address
        self.thread = None

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/#{self.token}"

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
        self.thread.start()
        return self

    def close(self):
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
        # 请求在关闭连接后退出，等它释放上传锁，避免回调已关闭的 Qt 窗口。
        if self.upload_lock.acquire(timeout=2):
            self.upload_lock.release()


def pending_uploads(inbox):
    items = []
    for info in sorted(Path(inbox).glob("*/.upload.json")):
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
            name = data["name"]
            if Path(name).name != name or name.startswith("."):
                continue
            path = info.parent / name
            if path.is_file() and not path.is_symlink():
                items.append(ImportItem(path, name, path.stat().st_size, file_kind(path),
                                        tags=normalize_tags(data.get("tags", [])), origin="手机上传"))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return items


def clean_upload(item, inbox):
    """仅删除 inbox 内本次成功导入的临时副本，不触及手机/本地源文件。"""
    parent, root = item.source.parent.resolve(), Path(inbox).resolve()
    if item.origin == "手机上传" and parent.parent == root and not item.source.is_symlink():
        item.source.unlink(missing_ok=True)
        (parent / ".upload.json").unlink(missing_ok=True)
        try:
            parent.rmdir()
        except OSError:
            pass
