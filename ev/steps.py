"""实验步骤：元数据存入 meta.json，图片作为实验内的独立副本保存。"""
from __future__ import annotations

import argparse
import html
import os
import shutil
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import quote

from . import experiment as ex
from .config import Ctx
from . import references as file_refs
from .rich_text import scientific_html
from .util import FileLock, human_time, now_iso, sha256_file, truncate

PREVIEW = "04_notes/实验步骤.html"


def add_record_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", nargs="*", help="步骤内容；可使用引号输入多行文本")
    parser.add_argument("-t", "--title", default=None, help="步骤标题")
    parser.add_argument("-i", "--image", action="append", default=[], help="图片路径，可重复使用")
    parser.add_argument("--edit", type=int, metavar="序号", help="修改已有步骤，图片默认追加")
    parser.add_argument("--clear-images", action="store_true", help="清除图片关联（保留磁盘文件）")


def add_list_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--brief", action="store_true", help="用表格列出全部步骤的序号、标题、时间和图片数")
    parser.add_argument("--step", type=int, metavar="序号", help="只查看指定步骤")
    parser.add_argument("--html", action="store_true", help="生成完整实验步骤的离线图文页面")
    parser.add_argument("--open", action="store_true", help="生成图文页面并在浏览器中打开")


def load(ctx: Ctx, rec: dict) -> list[dict]:
    meta = ex.load_meta(ex.exp_dir(ctx, rec))
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验 meta.json 缺失或无效，请先恢复元数据")
    return meta.get("steps", [])


def select(items: list[dict], number: int | None = None) -> list[dict]:
    if number is None:
        return items
    found = [s for s in items if s["number"] == number]
    if not found:
        raise ValueError(f"找不到步骤 {number}")
    return found


def _validate_image(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"图片不存在或不是文件：{path}")
    with path.open("rb") as f:
        head = f.read(16)
    valid = {
        ".png": head.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": head.startswith(b"\xff\xd8\xff"),
        ".jpeg": head.startswith(b"\xff\xd8\xff"),
        ".gif": head.startswith((b"GIF87a", b"GIF89a")),
        ".webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
        ".bmp": head.startswith(b"BM"),
        ".tif": head.startswith((b"II*\x00", b"MM\x00*")),
        ".tiff": head.startswith((b"II*\x00", b"MM\x00*")),
    }
    if not valid.get(path.suffix.lower(), False):
        raise ValueError(f"不支持的图片格式或文件头无效：{path.name}（支持 PNG/JPEG/GIF/WebP/BMP/TIFF）")


def _register_written(ctx: Ctx, rec: dict, paths: list[Path]) -> None:
    """生成文件必须立即重新哈希，避免快速编辑时复用同大小旧哈希。"""
    folder = ex.exp_dir(ctx, rec)
    for path in paths:
        st = path.stat()
        rel = path.relative_to(folder).as_posix()
        ctx.db.upsert_file(rec["id"], {
            "relpath": rel, "size": st.st_size, "mtime": st.st_mtime,
            "sha256": sha256_file(path), "role": ctx.role_for(rel),
        })
    ctx.db.commit()


def _storage_path(folder: Path, relative: str) -> Path:
    path = folder / relative
    if folder.resolve() not in path.resolve().parents:
        raise ValueError(f"实验记录目录指向实验外部，无法写入：{relative}")
    return path


def record(ctx: Ctx, rec: dict, *, text: str | None = None, title: str | None = None,
           images: list[str] | None = None, edit: int | None = None,
           clear_images: bool = False, references: list[dict] | None = None) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    sources = [Path(p).expanduser().resolve() for p in (images or [])]
    resolved = file_refs.resolve(ctx, rec, references) if references is not None else None
    # 全部验证通过后才复制，错误输入不会留下半条步骤。
    for src in sources:
        _validate_image(src)
    if clear_images and edit is None:
        raise ValueError("--clear-images 需要同时指定 --edit 序号")
    if edit is not None and text is None and title is None and not sources and not clear_images and references is None:
        raise ValueError("请提供要修改的内容、标题或图片")
    with FileLock(folder / ".expvault" / "steps.lock"):
        meta = ex.load_meta(folder)
        if not meta or meta.get("id") != rec["id"]:
            raise ValueError("实验 meta.json 缺失或无效，请先恢复元数据")
        items = meta.setdefault("steps", [])
        old = select(items, edit)[0] if edit is not None else None
        number = old["number"] if old else max((s["number"] for s in items), default=0) + 1
        stamp = now_iso()
        step = dict(old) if old else {
            "sync_id": uuid.uuid4().hex,
            "number": number, "title": f"步骤 {number}", "text": "",
            "created_at": stamp, "author": rec.get("operator") or ctx.cfg.get("operator", ""),
        }
        if text is not None:
            step["text"] = text.strip()
        if title is not None:
            step["title"] = title.strip() or f"步骤 {number}"
        step["images"] = [] if clear_images else list(step.get("images", []))
        if resolved is not None:
            step["references"] = resolved
        if not step["text"] and not sources and not step["images"] and not step.get("references"):
            raise ValueError("步骤内容、图片或引用资料至少填写一项")
        step["updated_at"] = stamp
        # 每次导入使用独立目录，同名图片不会覆盖之前的记录。
        batch = _storage_path(folder, f"04_notes/steps/{number:03d}/{uuid.uuid4().hex[:12]}")
        stage = batch.with_name("." + batch.name + ".part")
        committed = False
        try:
            if sources:
                stage.mkdir(parents=True)
                for i, src in enumerate(sources, 1):
                    name = f"{i:02d}{src.suffix.lower()}"
                    shutil.copy2(src, stage / name)
                    step["images"].append({"path": (batch / name).relative_to(folder).as_posix(),
                                           "name": src.name})
                os.replace(stage, batch)
            if old:
                items[items.index(old)] = step
            else:
                items.append(step)
            action = "step_edit" if old else "step_add"
            detail = f"#{number} {truncate(step['title'], 60)}"
            meta["updated_at"] = stamp
            ex.record_history(ctx, meta, action, detail, ts=stamp)
            ex.save_meta(folder, meta)
            committed = True
        finally:
            if stage.exists():
                shutil.rmtree(stage)
            if not committed and batch.exists():
                shutil.rmtree(batch)
        ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
        written = [folder / ex.META_NAME]
        if (folder / PREVIEW).exists():
            _write_preview(folder, meta)
            written.append(folder / PREVIEW)
        ex.scan_files(ctx, folder, meta)
        _register_written(ctx, rec, written)
        ex.write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False), folder=folder)
        ex.log_event(ctx, action, rec["id"], detail)
        return step


def markdown(items: list[dict]) -> str:
    if not items:
        return '_（暂无步骤，使用 `step` 记录操作和图片。）_'
    lines = []
    for step in items:
        title = scientific_html(str(step["title"]).replace("\n", " "))
        lines.extend([f"### {step['number']}. {title}", "",
                      f"> {human_time(step['created_at'])} · {step.get('author') or '未署名'}", "",
                      scientific_html(step["text"]), ""])
        for i, img in enumerate(step.get("images", []), 1):
            lines.extend([f"![步骤 {step['number']} · 图片 {i}]({quote(img['path'], safe='/')})", ""])
        if step.get("references"):
            lines.extend(["引用资料：", "", file_refs.markdown(step["references"]), ""])
    return "\n".join(lines)


def display(con, items: list[dict], *, brief: bool = False) -> None:
    if not items:
        con.tip('还没有实验步骤。试试：step "清洗样品，氮气吹干" --image "照片.png"')
        return
    if brief:
        con.table([[str(item["number"]), item["title"], human_time(item["created_at"]),
                    str(len(item.get("images", [])))] for item in items],
                  ["序号", "步骤", "记录时间", "图片"], ["right", "left", "left", "right"])
        con.tip(f"共 {len(items)} 个步骤；steps --step 序号 查看详情")
        return
    for step in items:
        con.header(f"{step['number']:02d}  {step['title']}")
        con.tip(f"{human_time(step['created_at'])} · {step.get('author') or '未署名'}"
                f" · {len(step.get('images', []))} 张图片")
        for line in step["text"].splitlines():
            con.info(f"  {line}")
        for img in step.get("images", []):
            con.detail(f"图片 · {img['name']}  {con.t.dim(img['path'])}")
    con.blank()
    con.tip("steps --open 打开完整图文记录（工作区内）")


def _write_preview(folder: Path, meta: dict) -> Path:
    esc = html.escape
    cards = []
    for step in meta.get("steps", []):
        figures = []
        for img in step.get("images", []):
            # 仅允许实验目录内的相对图片路径。
            target = (folder / img["path"]).resolve()
            if folder.resolve() not in target.parents:
                continue
            url = quote(os.path.relpath(target, folder / "04_notes").replace("\\", "/"), safe="/")
            caption = esc(img["name"])
            if target.exists():
                figures.append(f'<figure><a href="{url}"><img loading="lazy" src="{url}" alt="{caption}"></a>'
                               f'<figcaption><a href="{url}">{caption}</a></figcaption></figure>')
            else:
                figures.append(f'<p class="missing">图片缺失：{caption}</p>')
        citations = []
        for item in step.get("references", []):
            url = file_refs.relative_url(item)
            target = folder / item.get("relative_path", "")
            label = f'{esc(item.get("name", item["path"]))} · {esc(item["exp_id"])}'
            citations.append(f'<li><a href="../{url}">{label}</a></li>' if url and target.is_file() else f'<li class="missing">引用文件缺失：{label}</li>')
        cards.append(f'<article><span class="number">STEP {step["number"]:02d}</span>'
                     f'<h2>{scientific_html(step["title"])}</h2><p class="meta">'
                     f'{esc(human_time(step["created_at"]))} · {esc(step.get("author") or "未署名")}</p>'
                     f'<div class="text">{scientific_html(step["text"])}</div><div class="images">'
                     + "".join(figures) + '</div>' + ('<h3>引用资料</h3><ul>'+"".join(citations)+'</ul>' if citations else '') + '</article>')
    title = esc(meta.get("title", "实验步骤"))
    content = "".join(cards) or '<article><p>尚未记录实验步骤。</p></article>'
    page = f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 实验步骤</title><style>
:root{{color-scheme:light dark;--bg:#faf9f6;--paper:#fff;--ink:#282722;--muted:#77746d;--line:#e7e4dd;--brand:#b65b38}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 system-ui,sans-serif}}
main{{max-width:1040px;margin:auto;padding:56px 24px}}header{{margin-bottom:36px}}h1{{font-size:36px;line-height:1.3;margin:12px 0}}
.brand,.number{{color:var(--brand);font:600 13px/1.5 ui-monospace,monospace;letter-spacing:.12em}}
.meta,figcaption{{color:var(--muted);font-size:13px}}article{{background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:28px;margin:20px 0}}
h2{{font-size:23px;margin:8px 0}}.text{{white-space:pre-wrap;overflow-wrap:anywhere;margin:20px 0}}
.images{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:20px}}
figure{{margin:0;min-width:0}}img{{display:block;width:100%;max-height:560px;object-fit:contain;border-radius:8px;background:var(--bg)}}
a{{color:inherit;overflow-wrap:anywhere}}figcaption{{margin-top:8px}}.missing{{color:var(--brand)}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1c1b19;--paper:#242320;--ink:#eeebe4;--muted:#aaa59a;--line:#3d3932;--brand:#e39a76}}}}
@media print{{article{{break-inside:avoid}}main{{padding:0}}}}
</style><main><header><div class="brand">EXPVAULT / LAB NOTEBOOK</div><h1>{scientific_html(meta.get("title", "实验步骤"))}</h1>
<p class="meta">{esc(meta['id'])} · {len(meta.get('steps', []))} 个步骤 · 更新于 {esc(human_time(meta.get('updated_at', '')))}</p>
</header>{content}<footer class="meta">图片保存在实验目录中 · expvault</footer></main></html>'''
    path = _storage_path(folder, PREVIEW)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, path)
    return path


def preview(ctx: Ctx, rec: dict, *, open_browser: bool = False) -> Path:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    with FileLock(folder / ".expvault" / "steps.lock"):
        meta = ex.load_meta(folder)
        if not meta or meta.get("id") != rec["id"]:
            raise ValueError("实验 meta.json 缺失或无效，请先恢复元数据")
        path = _write_preview(folder, meta)
        _register_written(ctx, rec, [path])
        ex.write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False), folder=folder)
    if open_browser:
        webbrowser.open(path.as_uri())
    return path
