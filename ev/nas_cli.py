"""Terminal entry for mounted NAS libraries; regular CLI queries default to read-only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import nas
from .nas_libraries import Connections

READ_COMMANDS={"list","ls","show","search","stats","current","ids","id","open","steps","snapshots","targets","issues","export","use","web","verify","doctor"}


def main(argv=None):
    parser=argparse.ArgumentParser(prog="expvault nas",description="连接系统已挂载的 NAS 实验库；主机编辑，其他电脑只读查看。")
    commands=parser.add_subparsers(dest="action")
    commands.add_parser("list",help="列出本机保存的 NAS 连接")
    connect=commands.add_parser("connect",help="选择 NAS 挂载目录并打开网页")
    connect.add_argument("path")
    connect.add_argument("--mode",choices=("writer","viewer"),default="viewer",help="writer 为编辑主机；默认 viewer 只读")
    connect.add_argument("--init",action="store_true",help="首次初始化或将已关闭的原库设为 NAS 库；仅 writer")
    connect.add_argument("--name",default="")
    opened=commands.add_parser("open",help="打开本机已保存的 NAS 连接")
    opened.add_argument("connection_id")
    opened.add_argument("--mode",choices=("writer","viewer"))
    for command in (connect,opened):
        command.add_argument("--port",type=int,default=0)
        command.add_argument("--no-open",action="store_true")
    unlock=commands.add_parser("unlock",help="确认原编辑主机已停止后解除遗留占用")
    unlock.add_argument("path")
    unlock.add_argument("--confirm-stopped",action="store_true")
    args=parser.parse_args(argv)
    if args.action in (None,"list"):
        items=Connections().list()
        if not items:print('还没有 NAS 连接。可在网页「NAS 实验库」中添加，或使用 nas connect。')
        for item in items:
            print(f'{item["id"]}  {item["name"]}  {"可访问" if item["available"] else "未连接"}  {item["mode"]}\n  {item["root"]}')
        return 0
    if args.action=="unlock":
        changed=nas.unlock(args.path,confirmed=args.confirm_stopped)
        print("NAS 遗留占用已解除" if changed else "这个 NAS 库没有编辑占用")
        return 0
    if args.action=="open":
        item=Connections().get(args.connection_id)
        root=Path(item["root"]);mode=args.mode or item["mode"];name=item["name"]
    else:
        root=nas.checked_root(args.path);mode=args.mode;name=args.name
        if args.init:
            if mode!="writer":raise ValueError("首次初始化请选择 --mode writer")
            root=nas.initialize(root,project=name or "NAS 实验库")
        nas.assert_available(root)
    with nas.access(root,mode):
        Connections().remember(root,mode,name)
        print("NAS 编辑主机" if mode=="writer" else "NAS 只读查看",str(root))
        from .web import run
        return run(root,port=args.port,open_browser=not args.no_open,nas_mode=mode)


def dispatch(cli_main, argv):
    argv=list(argv)
    try:
        if argv and argv[0]=="nas":return main(argv[1:])
        mode="writer" if "--nas-write" in argv else "viewer"
        argv=[value for value in argv if value!="--nas-write"]
        root=None;command="";index=0
        while index<len(argv):
            value=argv[index]
            if value=="--root" and index+1<len(argv):root=Path(argv[index+1]);index+=2;continue
            if value.startswith("--root="):root=Path(value.split("=",1)[1]);index+=1;continue
            if not value.startswith('-'):
                command=value;break
            index+=1
        from . import config
        root=(root or config.find_root()).expanduser().resolve()
        if not nas.is_nas(root):return cli_main(argv)
        if not command:command="list";argv.append(command)
        if mode=="viewer" and command not in READ_COMMANDS:
            raise ValueError("NAS 当前为只读连接；编辑主机执行写入时请加 --nas-write，或使用 nas connect --mode writer")
        with nas.access(root,mode):
            result=cli_main(argv)
            if mode=="writer" and not result:
                ctx=config.load(root)
                try:nas.publish_checksums(ctx)
                finally:
                    if ctx._db is not None:ctx.db.close()
            return result
    except (ValueError,OSError) as exc:
        if "--json" in argv:
            print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False))
        else:print(str(exc),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(dispatch(lambda _args:0,["nas",*sys.argv[1:]]))
