"""Per-computer NAS connection shortcuts and owned web sessions."""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from filelock import FileLock

from . import nas
from .util import write_json


class Connections:
    def __init__(self):
        self.directory=nas.storage_root()
        self.directory.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.path=self.directory/"nas-connections.json"
        self.lock=FileLock(str(self.directory/"nas-connections.lock"),timeout=5)

    def _read(self):
        if not self.path.exists():return []
        value=nas.read_object(self.path)
        if value.get("version")!=1 or not isinstance(value.get("items"),list):
            raise ValueError("本机 NAS 连接清单无效，不会自动覆盖")
        return value["items"]

    def list(self):
        with self.lock:
            result=[]
            for item in self._read():
                value=dict(item)
                try:
                    nas.assert_available(item["root"],item["vault_id"])
                    value["available"]=True
                except (OSError,ValueError):
                    value["available"]=False
                result.append(value)
            return result

    def remember(self,root,mode,name=""):
        root=nas.checked_root(root)
        value=nas.assert_available(root)
        with self.lock:
            items=self._read()
            old=next((item for item in items if item["root"]==str(root)),None)
            if old and old["vault_id"]!=value["id"]:
                raise ValueError("这个路径的 NAS 实验库已改变，请核对原库后再连接")
            item={"id":old["id"] if old else uuid.uuid4().hex,"name":name.strip() or (old or {}).get("name") or nas.read_object(root/"config.json").get("project") or root.name,
                "root":str(root),"mode":mode,"vault_id":value["id"]}
            items=[entry for entry in items if entry["id"]!=item["id"]]+[item]
            write_json(self.path,{"version":1,"items":items})
            return item

    def get(self,identity):
        item=next((item for item in self.list() if item["id"]==identity),None)
        if not item:raise ValueError("本机没有这条 NAS 连接，请重新选择挂载目录")
        if not item["available"]:raise ValueError("NAS 连接不可访问，请先在系统中重新挂载")
        return item


class LibrarySessions:
    def __init__(self,owner):
        self.owner=owner
        self._connections=None
        self.lock=threading.RLock()
        self.sessions={}
        self.closed=False

    @property
    def connections(self):
        if self._connections is None:
            self._connections=Connections()
        return self._connections

    def list(self):
        return {"items":self.connections.list(),"home_url":self.owner.url,"current_root":str(self.owner.service.root)}

    def open(self,*,root="",mode="viewer",name="",initialize=False,connection_id=""):
        from .web import WebServer
        if mode not in ("writer","viewer") or not isinstance(initialize,bool):
            raise ValueError("NAS 连接方式无效")
        if not isinstance(root,str) or len(root)>4096 or not isinstance(name,str) or len(name)>80 or any(not char.isprintable() for char in name):
            raise ValueError("NAS 目录或连接名称无效")
        if connection_id:
            root=self.connections.get(connection_id)["root"]
        folder=nas.checked_root(root)
        if nas.home(folder)!=folder:
            raise ValueError("请连接 NAS 实验库根目录，再从工作空间列表切换项目")
        key=(str(folder),mode)
        with self.lock:
            if self.closed:raise ValueError("主窗口已关闭，请重新启动")
            if initialize:
                if mode!="writer":raise ValueError("首次建立 NAS 实验库请选择编辑主机")
                if folder==self.owner.service.root and not self.owner.service.nas_mode:
                    raise ValueError("请先关闭正在使用该目录的本地程序，再将原库设为 NAS 库")
                folder=nas.initialize(folder,project=name or "NAS 实验库")
            else:
                nas.assert_available(folder)
            server=None
            if self.owner.service.root==folder and self.owner.service.nas_mode==mode:
                server=self.owner
            elif key in self.sessions:
                server=self.sessions[key][0]
            created=False
            if server is None:
                access=nas.access(folder,mode)
                access.__enter__()
                try:
                    server=WebServer(folder,host=self.owner.host,port=0,nas_mode=mode,libraries=self).start()
                except BaseException:
                    access.__exit__(None,None,None)
                    raise
                self.sessions[key]=(server,access)
                created=True
            try:
                item=self.connections.remember(folder,mode,name)
            except BaseException:
                if created:
                    self.sessions.pop(key,None)
                    try:server.close()
                    finally:access.__exit__(None,None,None)
                raise
            return {"url":server.url,"connection":item}

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            sessions=list(self.sessions.values())
            self.sessions.clear()
        for server,access in sessions:
            try:server.close()
            finally:access.__exit__(None,None,None)
