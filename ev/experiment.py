"""实验记录的创建、登记、扫描、校验、检索。"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
import shutil
from pathlib import Path

from .config import Ctx
from .util import (
    append_jsonl,
    ensure_dir,
    hr,
    human_size,
    human_time,
    now_iso,
    posix,
    read_json,
    rel_posix,
    sha256_file,
    slugify,
    table,
    today,
    truncate,
    write_json,
)

META_NAME = "meta.json"
README_NAME = "README.md"
MANUAL_BEGIN = "<!-- ===== 以下为手工补充区，重新生成时保留 ===== -->"

STATUS_LABEL = {
    "planned": "计划中",
    "running": "进行中",
    "done": "已完成",
    "aborted": "已中止",
    "archived": "已归档",
}


# --------------------------------------------------------------------------
# 路径与元数据
# --------------------------------------------------------------------------
def exp_dir(ctx: Ctx, rec: dict) -> Path:
    return ctx.root / rec["folder"]


def meta_path(ctx: Ctx, rec: dict) -> Path:
    return exp_dir(ctx, rec) / META_NAME


def load_meta(folder: Path) -> dict | None:
    return read_json(folder / META_NAME, None)


def save_meta(folder: Path, meta: dict) -> None:
    write_json(folder / META_NAME, meta)


def find_meta_files(ctx: Ctx) -> list[Path]:
    """递归找所有实验目录的 meta.json（跳过隐藏目录）。"""
    out: list[Path] = []
    if not ctx.data_dir.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(ctx.data_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if META_NAME in filenames:
            out.append(Path(dirpath) / META_NAME)
            # Raw instrument exports may also contain meta.json; an experiment owns its subtree.
            dirnames[:] = []
    return sorted(out)


def is_excluded(relpath: str, patterns: list[str]) -> bool:
    name = relpath.rsplit("/", 1)[-1]
    parts = relpath.split("/")
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(relpath, pat):
            return True
        if pat in parts:
            return True
    return False


# --------------------------------------------------------------------------
# README 渲染
# --------------------------------------------------------------------------
def render_readme(ctx: Ctx, meta: dict, files: list[dict] | None = None) -> str:
    from .steps import markdown
    from .rich_text import scientific_html, notes_html
    from .measurements import comparison_html

    files = files or []
    p = meta.get("params") or {}

    def kv_table(d: dict) -> str:
        if not d:
            return "_（未填写）_"
        rows = ["| 参数 | 值 |", "| --- | --- |"]
        for k, v in d.items():
            rows.append(f"| {scientific_html(k)} | {scientific_html(v)} |")
        return "\n".join(rows)

    by_role: dict[str, list[dict]] = {}
    for f in files:
        by_role.setdefault(f.get("role", "other"), []).append(f)

    role_names = {r: label for _, label, r in ctx.structure()}
    role_names["meta"] = "元数据"

    tree_lines = []
    for name, label, role in ctx.structure():
        items = by_role.get(role, [])
        tree_lines.append(f"**{name}/** — {label}（{len(items)} 个文件）")
        for it in items[:40]:
            labels = " · 标签：" + "、".join(it["tags"]) if it.get("tags") else ""
            tree_lines.append(f"- `{it['relpath']}`  {human_size(it.get('size', 0))}{labels}")
        if len(items) > 40:
            tree_lines.append(f"- …… 其余 {len(items) - 40} 个文件见 `expvault show {meta['id']}`")
        tree_lines.append("")

    tags = meta.get("tags") or []
    tag_str = "、".join(tags) if isinstance(tags, list) else str(tags)

    return f"""# {scientific_html(meta.get('title', '(无标题)'))}

> 🤖 本文件由 expvault 自动生成，请勿手工编辑（重新生成会覆盖）。
> 手写记录请写在 `04_notes/日志.md`，或运行 `expvault note {meta['id']} "内容"`。

| 项目 | 内容 |
| --- | --- |
| 实验编号 | `{meta.get('id', '')}` |
| 日期 | {meta.get('date', '')} |
| 状态 | {STATUS_LABEL.get(meta.get('status'), meta.get('status', ''))} |
| 所属课题 | {meta.get('project', '') or '—'} |
| 器件 / 样品 | {scientific_html(meta.get('device', '')) or '—'} |
| 工艺批次 | {meta.get('batch', '') or '—'} |
| 类别 | {meta.get('category', '') or '—'} |
| 标签 | {tag_str or '—'} |
| 实验分组 | {scientific_html('、'.join(meta.get('groups') or [])) or '—'} |
| 操作人 | {meta.get('operator', '') or '—'} |
| 建立时间 | {human_time(meta.get('created_at', ''))} |
| 最后更新 | {human_time(meta.get('updated_at', ''))} |

## 一、实验目的

{scientific_html(meta.get('purpose', '')) or '_待补充：这次想验证什么？预期结果是什么？_'}

## 二、样品与工艺参数

{kv_table(p)}

## 仪器与对照测量

{comparison_html(meta.get('measurement_groups', [])) or '_（尚未记录仪器与测量）_'}

## 三、测试条件

{scientific_html(meta.get('conditions', '')) or '_待补充：仪器型号、扫描范围、步长、气氛、温度……_'}

## 实验步骤

{markdown(meta.get('steps', []))}

## 四、结果与观察

{scientific_html(meta.get('results', '')) or '_待补充：关键数据、曲线特征、异常现象。_'}

## 五、结论与下一步

{scientific_html(meta.get('conclusion', '')) or '_待补充。_'}

## 实验备注

{notes_html(meta.get('notes', '')) or '_（未填写）_'}

## 六、文件清单

{chr(10).join(tree_lines) if tree_lines else '_（暂无文件）_'}

## 七、变更记录

{chr(10).join(f"- {human_time(e['ts'])}  {scientific_html(e['action'])}  {scientific_html(e['detail'])}" for e in reversed(meta.get('history', [])[-20:])) or '_（暂无）_'}

{MANUAL_BEGIN}

## 八、我的补充

_（随便写，`expvault doc` 重新生成时会保留本段之后的内容）_
"""


def write_readme(ctx: Ctx, meta: dict, files: list[dict] | None = None,
                 folder: Path | None = None) -> None:
    from .nas import require_writer
    require_writer(ctx)
    if folder is None:
        rel = meta.get("folder") or ""
        if not rel:
            rec = ctx.db.get_experiment(meta.get("id", ""))
            rel = rec["folder"] if rec else ""
        folder = ctx.root / rel
    path = folder / README_NAME
    old = ""
    if path.exists():
        try:
            old = path.read_text(encoding="utf-8")
        except Exception:
            old = ""
    tail = ""
    if MANUAL_BEGIN in old:
        tail = old.split(MANUAL_BEGIN, 1)[1]
    body = render_readme(ctx, meta, files)
    body = body.split(MANUAL_BEGIN)[0]
    text = body + MANUAL_BEGIN + (tail or "\n\n## 八、我的补充\n\n_（随便写）_\n")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)

    # README 刚被重写，顺手把它的新哈希补进索引，否则 verify 会误报「内容已变化」
    exp_id = meta.get("id", "")
    if exp_id and ctx.db.get_experiment(exp_id):
        try:
            st = path.stat()
            ctx.db.upsert_file(exp_id, {
                "relpath": README_NAME,
                "size": int(st.st_size),
                "mtime": float(st.st_mtime),
                "sha256": sha256_file(path),
                "role": "meta",
            })
            ctx.db.commit()
        except OSError:
            pass


# --------------------------------------------------------------------------
# 创建
# --------------------------------------------------------------------------
def folder_name(ctx: Ctx, exp_id: str, slug: str) -> str:
    naming = ctx.cfg.get("naming", {})
    date_part = exp_id[1:9] if exp_id.startswith("E") and exp_id[1:9].isdigit() else ""
    if naming.get("date_prefix") and date_part:
        d = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
        return f"{d}_{exp_id}_{slug}"
    return f"{exp_id}_{slug}"


def existing_seq(ctx: Ctx, date_str: str, prefix: str = "E") -> int:
    """数据库与磁盘目录里出现过的最大序号（防止重建索引后编号冲突）。"""
    compact = date_str.replace("-", "")
    stem = f"{prefix}{compact}-"
    seq = 0

    def scan(name: str) -> None:
        nonlocal seq
        if name.startswith(stem):
            tail = name[len(stem):].split("_")[0].split("-")[0]
            if tail.isdigit():
                seq = max(seq, int(tail))

    for rec in ctx.db.conn.execute(
        "SELECT id FROM experiments WHERE id LIKE ?", (stem + "%",)
    ).fetchall():
        scan(str(rec["id"]))
    year_dir = ctx.data_dir / date_str[:4]
    if year_dir.exists():
        for p in year_dir.iterdir():
            if p.is_dir():
                scan(p.name.split("_")[0])
    return seq


def create(
    ctx: Ctx,
    title: str,
    *,
    date_str: str | None = None,
    project: str = "",
    operator: str = "",
    device: str = "",
    batch: str = "",
    category: str = "",
    status: str = "running",
    tags: list[str] | None = None,
    params: dict | None = None,
    slug: str = "",
    notes: str = "",
    purpose: str = "",
    conditions: str = "",
    measurement_groups: list | None = None,
    groups: list[str] | None = None,
    _creation_log: list | None = None,
) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    import uuid
    from .measurements import normalize
    from .groups import normalize_names
    groups = normalize_names(groups)
    measurement_groups = normalize(measurement_groups if measurement_groups is not None else [])
    date_str = date_str or today()
    prefix = ctx.cfg.get("naming", {}).get("id_prefix", "E")
    naming = ctx.cfg.get("naming", {})
    if naming.get("ascii_only", True):
        slug = slugify(slug or title)
    slug = (slug or "exp")[: int(naming.get("slug_max", 40))]

    year = date_str[:4]
    base = ctx.data_dir / year
    ensure_dir(base)

    seq = existing_seq(ctx, date_str, prefix) + 1
    while True:
        exp_id = f"{prefix}{date_str.replace('-', '')}-{seq:03d}"
        folder = base / folder_name(ctx, exp_id, slug)
        if not folder.exists() and ctx.db.get_experiment(exp_id) is None:
            break
        seq += 1
    ensure_dir(folder)
    if _creation_log is not None:
        _creation_log.append({"id":exp_id,"folder":folder})

    for dname, _label, _role in ctx.structure():
        ensure_dir(folder / dname)

    meta = {
        "schema": 1,
        "sync_id": uuid.uuid4().hex,
        "id": exp_id,
        "title": title,
        "date": date_str,
        "project": project or ctx.cfg.get("project", ""),
        "operator": operator or ctx.cfg.get("operator", ""),
        "device": device,
        "batch": batch,
        "category": category,
        "status": status,
        "tags": tags or [],
        "params": params or {},
        "measurement_groups": measurement_groups,
        "groups": groups,
        "notes": notes,
        "purpose": purpose,
        "conditions": conditions,
        "results": "",
        "conclusion": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "history": [{"ts": now_iso(), "action": "create", "detail": title}],
    }
    save_meta(folder, meta)

    rec = dict(meta)
    rec["folder"] = posix(os.path.relpath(folder, ctx.root))
    ctx.db.upsert_experiment(rec)
    ctx.db.log("create", exp_id, f"{title}")
    append_jsonl(ctx.log_path, {"ts": now_iso(), "action": "create", "exp_id": exp_id, "detail": title})

    rec = ctx.db.get_experiment(exp_id)
    write_readme(ctx, meta, [], folder=folder)
    scan_files(ctx, folder, meta)      # 把 meta.json / README.md 也登记进去
    return ctx.db.get_experiment(exp_id) or rec or {}


# --------------------------------------------------------------------------
# 元数据同步（目录 -> 数据库）
# --------------------------------------------------------------------------
def register_from_disk(ctx: Ctx, folder: Path) -> dict | None:
    meta = load_meta(folder)
    if not meta:
        return None
    meta.setdefault("id", folder.name.split("_")[0])
    meta["folder"] = rel_posix(folder, ctx.root)
    ctx.db.upsert_experiment(meta)
    return meta


def scan_files(ctx: Ctx, folder: Path, meta: dict, *, rehash: bool = False) -> dict:
    """登记目录内文件；mtime+size 未变时复用已有哈希，避免重复读大文件。"""
    exp_id = meta.get("id", "")
    if not exp_id:
        raise ValueError(f"实验目录缺少有效的 meta.json，无法登记：{folder}")
    patterns = ctx.exclude_patterns()
    known = {f["relpath"]: f for f in ctx.db.files_of(exp_id)}
    file_tags = meta.get("file_tags", {})
    seen: list[str] = []
    stats = {"new": 0, "changed": 0, "same": 0, "bytes": 0, "hashed_bytes": 0}

    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = rel_posix(p, folder)
            if rel.startswith(".expvault"):
                continue
            if is_excluded(rel, patterns):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            size, mtime = int(st.st_size), float(st.st_mtime)
            is_link = 1 if (os.path.islink(p) or st.st_nlink > 1) else 0
            stats["bytes"] += size
            seen.append(rel)
            old = known.get(rel)
            if (
                old
                and not rehash
                and old["sha256"]
                and int(old["size"]) == size
                and abs(float(old["mtime"]) - mtime) < 2
            ):
                ctx.db.upsert_file(exp_id, {"relpath": rel, "size": size, "mtime": mtime,
                                            "sha256": old["sha256"], "role": ctx.role_for(rel),
                                            "is_link": is_link,
                                            "tags": file_tags.get(rel, []),
                                            "registered_at": old["registered_at"]})
                stats["same"] += 1
                continue
            digest = sha256_file(p)
            stats["hashed_bytes"] += size
            if old is None:
                stats["new"] += 1
            elif old["sha256"] != digest:
                stats["changed"] += 1
            ctx.db.upsert_file(exp_id, {"relpath": rel, "size": size, "mtime": mtime,
                                        "sha256": digest, "role": ctx.role_for(rel),
                                        "is_link": is_link, "tags": file_tags.get(rel, [])})
    missing = ctx.db.mark_missing(exp_id, seen)
    ctx.db.commit()
    stats["missing"] = missing
    stats["count"] = len(seen)
    return stats


def sync(ctx: Ctx, *, rehash: bool = False, adopt: bool = False) -> dict:
    metas = find_meta_files(ctx)
    total = {"experiments": 0, "new_files": 0, "changed": 0, "missing": 0, "bytes": 0,
             "adopted": 0, "orphans": 0, "orphan_samples": []}
    exp_folders: list[Path] = []
    for mp in metas:
        folder = mp.parent
        exp_folders.append(folder.resolve())
        meta = load_meta(folder) or {}
        existed = ctx.db.get_experiment(meta.get("id", ""))
        register_from_disk(ctx, folder)
        meta = load_meta(folder) or {}
        if not existed:
            total["adopted"] += 1
            ctx.db.log("adopt", meta.get("id", ""), "从磁盘 meta.json 登记")
        st = scan_files(ctx, folder, meta, rehash=rehash)
        total["experiments"] += 1
        total["new_files"] += st["new"]
        total["changed"] += st["changed"]
        total["missing"] += st["missing"]
        total["bytes"] += st["bytes"]

    # 找出「放在 数据\ 里、但不属于任何实验」的文件
    if ctx.data_dir.exists():
        for dirpath, dirnames, filenames in os.walk(ctx.data_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            here = Path(dirpath).resolve()
            if any(here == f or f in here.parents for f in exp_folders):
                dirnames[:] = []
                continue
            for fn in filenames:
                if is_excluded(fn, ctx.exclude_patterns()):
                    continue
                total["orphans"] += 1
                if len(total["orphan_samples"]) < 5:
                    total["orphan_samples"].append(rel_posix(Path(dirpath) / fn, ctx.data_dir))

    ctx.db.log("sync", "", json.dumps({k: v for k, v in total.items() if k != "orphan_samples"},
                                      ensure_ascii=False))
    return total


# --------------------------------------------------------------------------
# 添加文件
# --------------------------------------------------------------------------
LINK_MODES = ("auto", "hard", "soft")


def _link_capability(dirpath: Path) -> tuple[bool, bool]:
    """在目标目录里探测能不能建硬链接 / 符号链接，避免做到一半才失败。"""
    ensure_dir(dirpath)
    probe = dirpath / ".expvault-link-probe"
    twin = dirpath / ".expvault-link-probe2"
    hard = soft = False
    try:
        probe.write_bytes(b"x")
        try:
            os.link(probe, twin)
            hard = True
        except OSError:
            pass
        finally:
            try:
                twin.unlink()
            except OSError:
                pass
        try:
            os.symlink(probe, twin)
            soft = True
        except OSError:
            pass
    except OSError:
        pass
    finally:
        for p in (probe, twin):
            try:
                p.unlink()
            except OSError:
                pass
    return hard, soft


def _remove_path(p: Path) -> None:
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def _is_empty_tree(p: Path) -> bool:
    """目录里一个文件都没有（只有空子目录也算空），用于识别上次失败留下的残骸。"""
    if not p.is_dir() or p.is_symlink():
        return False
    for _dp, _dn, filenames in os.walk(p):
        if filenames:
            return False
    return True


def _link_one(src: Path, dest: Path, mode: str) -> str:
    """给单个文件建链接；返回实际用上的方式（hard / soft / copy）。"""
    if mode in ("auto", "hard"):
        try:
            os.link(src, dest)
            return "hard"
        except OSError:
            if mode == "hard":
                raise
    if mode in ("auto", "soft"):
        try:
            os.symlink(src, dest)
            return "soft"
        except OSError as e:
            if mode == "soft":
                if getattr(e, "winerror", None) == 1314:
                    raise OSError(
                        "系统拒绝创建符号链接（需要 Windows「开发者模式」或管理员权限）；"
                        "可以改用 --link 建硬链接，或直接复制"
                    ) from e
                raise
    shutil.copy2(src, dest)
    return "copy"


def _link_tree(src: Path, dest: Path, mode: str, stats: dict) -> None:
    """目录：只建目录骨架 + 每个文件一个链接，不复制内容。"""
    ensure_dir(dest)
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".ipynb_checkpoints")]
        for fn in filenames:
            sp = Path(dirpath) / fn
            dp = dest / sp.relative_to(src)
            ensure_dir(dp.parent)
            if dp.exists():
                stats["skip"] = stats.get("skip", 0) + 1
                continue
            kind = _link_one(sp, dp, mode)
            stats[kind] = stats.get(kind, 0) + 1


def add_files(ctx: Ctx, rec: dict, paths: list[Path], role: str = "", move: bool = False,
              link: str = "") -> dict:
    """把文件加入实验。

    link:
        ""      复制（默认，最安全）
        "auto"  先试硬链接，不行再试符号链接，最后退回复制
        "hard"  只建硬链接（同盘、不占额外空间，改一个两边都变）
        "soft"  只建符号链接（可跨盘，但源文件一移走就断）
    """
    from .nas import require_writer
    require_writer(ctx)
    folder = exp_dir(ctx, rec)
    results = {"copied": [], "skipped": [], "errors": [], "already": [],
               "link_stats": {"hard": 0, "soft": 0, "copy": 0, "skip": 0}}
    role = role or "raw"          # 默认当原始数据放 01_raw，最不容易放错
    target_sub = None
    if role:
        for name, _label, r in ctx.structure():
            if r == role:
                target_sub = name

    # 链接模式先在目标目录做一次能力探测：不行就直接报错，什么都不留下
    if link:
        probe_dir = folder / (target_sub or "")
        ensure_dir(probe_dir)
        hard_ok, soft_ok = _link_capability(probe_dir)
        if link == "hard" and not hard_ok:
            results["errors"].append(
                "无法创建硬链接：源和目标必须在同一个 NTFS 卷上；跨盘请用 --link-soft 或直接复制")
            return results
        if link == "soft" and not soft_ok:
            results["errors"].append(
                "系统拒绝创建符号链接（需要 Windows「开发者模式」或管理员权限）；"
                "可以改用 --link 建硬链接，或直接复制")
            return results
        if link == "auto":
            link = "hard" if hard_ok else ("soft" if soft_ok else "copy")

    for src in paths:
        src = Path(src)
        if not src.exists():
            results["errors"].append(f"不存在：{src}")
            continue
        try:
            src_res = src.resolve()
        except OSError:
            src_res = src
        folder_res = folder.resolve()
        if src_res == folder_res or folder_res in src_res.parents:
            # 已经在实验目录里了：不复制，只登记（避免把实验目录拷进它自己）
            results["already"].append(rel_posix(src, folder))
            continue
        if src_res in folder_res.parents:
            results["errors"].append(f"{src} 是实验目录的上级，不能导入")
            continue
        dest_root = folder / (target_sub or "")
        dest = dest_root / src.name
        try:
            if dest.exists():
                # 上次失败留下的空目录/空骨架不算「已存在」，清掉重来
                if link and _is_empty_tree(dest):
                    _remove_path(dest)
                elif link:
                    results["skipped"].append(f"{src}（目标已存在）")
                    continue
            ensure_dir(dest_root)
            if link:
                # 先建在 .part 临时位置，成功后再改名，失败不留残留
                tmp = dest.with_name(dest.name + ".part")
                _remove_path(tmp)
                try:
                    if src.is_dir():
                        _link_tree(src, tmp, link, results["link_stats"])
                    else:
                        kind = _link_one(src, tmp, link)
                        results["link_stats"][kind] = results["link_stats"].get(kind, 0) + 1
                    os.replace(tmp, dest)
                except Exception:
                    _remove_path(tmp)
                    raise
                suffix = "/" if src.is_dir() else ""
                results["copied"].append(f"{src} -> {rel_posix(dest, folder)}{suffix}")
            elif src.is_dir():
                if dest.exists():
                    results["skipped"].append(f"{src}（目标已存在）")
                    continue
                shutil.copytree(src, dest) if not move else shutil.move(str(src), str(dest))
                results["copied"].append(f"{src} -> {rel_posix(dest, folder)}/")
            else:
                if dest.exists():
                    dest = dest_root / f"{src.stem}_{now_iso().replace(':', '')[:15]}{src.suffix}"
                shutil.move(str(src), str(dest)) if move else shutil.copy2(src, dest)
                results["copied"].append(f"{src} -> {rel_posix(dest, folder)}")
        except Exception as e:
            results["errors"].append(f"{src}: {e}")

    meta = load_meta(folder) or {}
    note = f"{len(results['copied'])} 个文件"
    if link:
        ls = results["link_stats"]
        note += f"（链接：硬 {ls.get('hard', 0)} / 软 {ls.get('soft', 0)}）"
    meta.setdefault("history", []).append({"ts": now_iso(), "action": "add", "detail": note})
    meta["updated_at"] = now_iso()
    save_meta(folder, meta)
    ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
    ctx.db.log("add", rec["id"], note)
    scan_files(ctx, folder, meta)
    write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False), folder=folder)
    return results


def expand_targets(folder: Path, patterns: list[str], *, recursive: bool = False) -> list[Path]:
    """把用户给的路径/通配符展开成实际路径（相对路径按实验目录解析）。

    - 支持 * ? ** 通配（** 需要 recursive=True 才会递归展开）
    - 已经被父目录包含的路径会被去掉，避免重复删除
    """
    hits: list[Path] = []
    for raw in patterns:
        text = str(raw).replace("\\", "/")
        p = Path(text)
        if not p.is_absolute():
            p = folder / text
        s = str(p)
        if any(ch in s for ch in "*?["):
            hits.extend(Path(h) for h in glob.glob(s, recursive=True))
        elif p.exists() or p.is_symlink():
            hits.append(p)
        else:
            hits.append(p)          # 不存在也保留，好让上层报告"找不到"

    # 去掉嵌套：如果要删 a/，就不必再删 a/b
    uniq = sorted({h for h in hits}, key=lambda x: (len(str(x)), str(x)))
    kept: list[Path] = []
    for h in uniq:
        if any(k == h or k in h.parents for k in kept):
            continue
        kept.append(h)
    return kept


def describe_targets(folder: Path, targets: list[Path]) -> tuple[int, int, int]:
    """统计要删的东西：文件数、目录数、字节数。"""
    files = dirs = total = 0
    for p in targets:
        try:
            if p.is_dir() and not p.is_symlink():
                dirs += 1
                for dp, _dn, fn in os.walk(p):
                    files += len(fn)
                    for f in fn:
                        try:
                            total += (Path(dp) / f).stat().st_size
                        except OSError:
                            pass
            else:
                files += 1
                total += p.stat().st_size if p.exists() else 0
        except OSError:
            pass
    return files, dirs, total


def remove_files(ctx: Ctx, rec: dict, patterns: list[str], *, delete_disk: bool = True,
                 recursive: bool = False, literal: bool = False) -> dict:
    """删除文件或目录（连同登记记录）。

    patterns 支持通配符；目录需要 recursive=True。
    """
    from .nas import require_writer
    require_writer(ctx)
    folder = exp_dir(ctx, rec)
    out = {"removed": [], "failed": [], "dirs": 0, "files": 0}
    targets = ([Path(p) if Path(p).is_absolute() else folder / p for p in patterns]
               if literal else expand_targets(folder, patterns, recursive=recursive))
    from .references import incoming
    referenced = incoming(ctx, rec["id"])

    for p in targets:
        rel = rel_posix(p, folder) if str(p).startswith(str(folder)) else str(p)
        try:
            if any(path == rel or path.startswith(rel.rstrip("/")+"/") for path in referenced):
                raise PermissionError("文件仍被步骤或日志引用，请先在记录中解除引用")
            if p.is_dir() and not p.is_symlink():
                if not recursive:
                    raise IsADirectoryError(f"{rel} 是目录，加 -r 才能删")
                n = sum(len(files) for _dp, _dn, files in os.walk(p))
                if delete_disk:
                    shutil.rmtree(p)
                ctx.db.conn.execute(
                    "DELETE FROM files WHERE exp_id=? AND (relpath=? OR relpath LIKE ? ESCAPE '\\')",
                    (rec["id"], rel, rel.rstrip("/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"),
                )
                out["removed"].append(rel.rstrip("/") + "/")
                out["dirs"] += 1
                out["files"] += n
            else:
                if not (p.exists() or p.is_symlink()):
                    raise FileNotFoundError(f"{rel} 不存在")
                if delete_disk:
                    p.unlink()
                ctx.db.conn.execute(
                    "DELETE FROM files WHERE exp_id=? AND relpath=?", (rec["id"], rel)
                )
                out["removed"].append(rel)
                out["files"] += 1
        except OSError as e:
            out["failed"].append(f"{rel}: {e}")
    ctx.db.commit()

    meta = load_meta(folder) or {}
    if out["removed"]:
        if "file_tags" in meta:
            meta["file_tags"] = {path: tags for path, tags in meta["file_tags"].items()
                                 if not any(path == removed or (removed.endswith("/") and path.startswith(removed))
                                            for removed in out["removed"])}
        detail = ", ".join(out["removed"][:6])
        if len(out["removed"]) > 6:
            detail += f" 等 {len(out['removed'])} 项"
        meta.setdefault("history", []).append(
            {"ts": now_iso(), "action": "rm", "detail": detail}
        )
        meta["updated_at"] = now_iso()
        save_meta(folder, meta)
        ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
        ctx.db.log("rm", rec["id"], detail)
        write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False), folder=folder)
    return out


# --------------------------------------------------------------------------
# 完整性校验
# --------------------------------------------------------------------------
def verify(ctx: Ctx, *, rehash: bool = True, exp_id: str = "") -> dict:
    """逐文件重新计算哈希并与登记值比对。这是「数据没悄悄坏掉」的核心保障。"""
    if exp_id:
        rec = ctx.db.find_experiment(exp_id)
        if not rec:
            raise ValueError(f"找不到实验：{exp_id}")
        targets = [rec]
    else:
        targets = ctx.db.all_experiments()

    report = {
        "checked": 0, "ok": 0, "modified": [], "missing": [],
        "unregistered": [], "bad_meta": [], "bytes": 0,
    }
    for rec in targets:
        folder = exp_dir(ctx, rec)
        if not folder.exists():
            report["bad_meta"].append(f"{rec['id']} 目录不存在：{rec['folder']}")
            continue
        if not (folder / META_NAME).exists():
            report["bad_meta"].append(f"{rec['id']} 缺少 meta.json")
        files = ctx.db.files_of(rec["id"], include_missing=True)
        seen = set()
        for f in files:
            p = folder / f["relpath"]
            seen.add(f["relpath"])
            report["checked"] += 1
            if not p.exists():
                report["missing"].append({"exp_id": rec["id"], "relpath": f["relpath"]})
                if not f["missing"]:
                    ctx.db.conn.execute(
                        "UPDATE files SET missing=1 WHERE exp_id=? AND relpath=?",
                        (rec["id"], f["relpath"]),
                    )
                continue
            st = p.stat()
            report["bytes"] += int(st.st_size)
            if f["missing"]:
                ctx.db.conn.execute(
                    "UPDATE files SET missing=0 WHERE exp_id=? AND relpath=?",
                    (rec["id"], f["relpath"]),
                )
            digest = sha256_file(p) if rehash else f["sha256"]
            from .nas import is_nas
            if is_nas(ctx) and not f["sha256"]:
                report.setdefault("unverified", []).append({"exp_id": rec["id"], "relpath": f["relpath"]})
                continue
            if digest != f["sha256"]:
                report["modified"].append(
                    {"exp_id": rec["id"], "relpath": f["relpath"],
                     "registered": f["sha256"][:12], "actual": digest[:12]}
                )
            else:
                report["ok"] += 1
        # 找出目录里有、库里没有的文件
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                rel = rel_posix(p, folder)
                if is_excluded(rel, ctx.exclude_patterns()):
                    continue
                if rel not in seen:
                    report["unregistered"].append({"exp_id": rec["id"], "relpath": rel})
    ctx.db.commit()
    return report


# --------------------------------------------------------------------------
# 修改
# --------------------------------------------------------------------------
EDITABLE = ["title", "date", "project", "operator", "device", "batch", "category",
            "status", "notes", "purpose", "conditions", "results", "conclusion"]


def update(ctx: Ctx, rec: dict, changes: dict) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    from .measurements import normalize
    if "measurement_groups" in changes:
        changes = {**changes, "measurement_groups": normalize(changes["measurement_groups"])}
    folder = exp_dir(ctx, rec)
    meta = load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配，无法编辑")
    if "groups" in changes:
        from .groups import normalize_names
        # Concurrent sync additions may exceed the editing limit; retain them and allow reductions.
        changes={**changes,"groups":normalize_names(changes["groups"], limit=max(20,len(meta.get("groups") or [])))}
    detail = []
    for k, v in changes.items():
        if v is None:
            continue
        if k == "tags":
            v = ([str(t).strip() for t in v if str(t).strip()] if isinstance(v, list)
                 else [t.strip() for t in str(v).replace("，", ",").split(",") if t.strip()])
        if k == "measurement_groups":
            meta[k] = v
            detail.append(f"仪器与测量更新：{len(v)} 台仪器，{sum(len(group['runs']) for group in v)} 组测量")
        elif k == "groups":
            meta[k]=v
            detail.append("实验分组="+", ".join(v))
        elif k == "params":
            meta["params"] = v if isinstance(v, dict) else meta.get("params", {})
            detail.append("params=" + json.dumps(meta["params"], ensure_ascii=False))
        elif k == "add_tag":
            meta["tags"] = [t.strip() for t in str(v).replace("，", ",").split(",") if t.strip()]
        elif k == "tags":
            meta["tags"] = v
        else:
            if k in EDITABLE:
                meta[k] = v
                detail.append(f"{k}={v}")
        if k in ("tags",):
            detail.append(f"tags={','.join(v)}")
    meta["updated_at"] = now_iso()
    meta.setdefault("history", []).append(
        {"ts": now_iso(), "action": "edit", "detail": "; ".join(detail)}
    )
    save_meta(folder, meta)
    ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
    path = folder / META_NAME
    st = path.stat()
    ctx.db.upsert_file(rec["id"], {"relpath": META_NAME, "size": st.st_size, "mtime": st.st_mtime,
                                   "sha256": sha256_file(path), "role": "meta"})
    ctx.db.log("edit", rec["id"], "; ".join(detail))
    write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False))
    return ctx.db.get_experiment(rec["id"]) or {}


def append_note(ctx: Ctx, rec: dict, text: str, author: str = "", *, references=None) -> Path:
    from .nas import require_writer
    require_writer(ctx)
    from . import journal
    folder = exp_dir(ctx, rec)
    notes_dir = folder / "04_notes"
    if folder.resolve() not in (notes_dir / "日志.md").resolve().parents:
        raise ValueError("日志目录越界")
    ensure_dir(notes_dir)
    path = notes_dir / "日志.md"
    who = author or rec.get("operator") or ctx.cfg.get("operator", "")
    record = journal.make_entry(ctx, rec, text, who, references)
    stamp = record["created_at"]
    entry = "\n" + journal.block(record) + "\n"
    if path.exists():
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(entry)
    else:
        path.write_text(f"# {rec['id']} 实验日志\n" + entry, encoding="utf-8", newline="\n")

    meta = load_meta(folder) or {}
    meta.setdefault("journal_entries", []).append(record)
    meta.setdefault("history", []).append({"ts": stamp, "action": "note", "detail": truncate(text, 60)})
    meta["updated_at"] = stamp
    save_meta(folder, meta)
    scan_files(ctx, folder, meta)
    write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False), folder=folder)
    ctx.db.log("note", rec["id"], truncate(text, 80))
    return path


def archive(ctx: Ctx, rec: dict, flag: bool = True) -> None:
    from .nas import require_writer
    require_writer(ctx)
    folder = exp_dir(ctx, rec)
    meta = load_meta(folder) or {}
    meta["archived"] = 1 if flag else 0
    meta["status"] = "archived" if flag else "done"
    meta["updated_at"] = now_iso()
    meta.setdefault("history", []).append({"ts": meta["updated_at"], "action": "archive" if flag else "unarchive",
                                           "detail": "归档实验，保留全部记录与文件" if flag else "恢复实验为已完成状态"})
    save_meta(folder, meta)
    ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
    ctx.db.log("archive" if flag else "unarchive", rec["id"], "")


def rebuild_index(ctx: Ctx) -> dict:
    """从磁盘上的 meta.json 完全重建索引；数据库丢了也不怕。"""
    metas = find_meta_files(ctx)
    n = 0
    for mp in metas:
        folder = mp.parent
        meta = load_meta(folder) or {}
        if not meta.get("id"):
            continue
        meta["folder"] = rel_posix(folder, ctx.root)
        ctx.db.upsert_experiment(meta)
        n += 1
    for mp in metas:
        folder = mp.parent
        meta = load_meta(folder) or {}
        if meta.get("id"):
            scan_files(ctx, folder, meta)
    ctx.db.log("rebuild", "", f"{n} 个实验")
    return {"experiments": n}


# --------------------------------------------------------------------------
# 导入已有目录
# --------------------------------------------------------------------------
def adopt(ctx: Ctx, src: Path, *, move: bool = False, title: str = "", **fields) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    src = Path(src).resolve()
    if not src.is_dir():
        raise ValueError(f"不是文件夹：{src}")
    title = title or src.name
    rec = create(ctx, title, **fields)
    folder = exp_dir(ctx, rec)
    for item in src.iterdir():
        dest = folder / item.name
        if item.name == META_NAME:
            continue
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True) if not move else shutil.move(str(item), str(dest))
        else:
            (shutil.move if move else shutil.copy2)(str(item), str(dest))
    meta = load_meta(folder) or {}
    scan_files(ctx, folder, meta)
    meta.setdefault("history", []).append(
        {"ts": now_iso(), "action": "adopt", "detail": f"从 {src} 导入"}
    )
    save_meta(folder, meta)
    ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
    write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False))
    return ctx.db.get_experiment(rec["id"]) or {}


# --------------------------------------------------------------------------
# 展示
# --------------------------------------------------------------------------
def format_list(ctx: Ctx, recs: list[dict]) -> str:
    rows = []
    for r in recs:
        rows.append([
            r["id"],
            r.get("date", ""),
            truncate(r.get("title", ""), 30),
            STATUS_LABEL.get(r.get("status", ""), r.get("status", "")),
            str(r.get("file_count", 0)),
            human_size(r.get("total_bytes", 0)),
            truncate(r.get("project", ""), 14),
        ])
    out = table(
        rows,
        ["编号", "日期", "标题", "状态", "文件", "占用", "课题"],
        ["left", "left", "left", "center", "right", "right", "left"],
    )
    total_b = sum(r.get("total_bytes", 0) for r in recs)
    out += f"\n\n共 {len(recs)} 个实验，{human_size(total_b)}"
    links = sum(r.get("link_count", 0) for r in recs)
    if links:
        out += f"\n其中 {links} 个文件是链接（逻辑大小大于实际占用）"
    return out


def format_show(ctx: Ctx, rec: dict) -> str:
    lines = []
    add = lines.append
    add(f"编号   {rec['id']}")
    add(f"标题   {rec.get('title', '')}")
    add(f"日期   {rec.get('date', '')}    状态  {STATUS_LABEL.get(rec.get('status'), rec.get('status'))}")
    add(f"课题   {rec.get('project') or '—'}    类别  {rec.get('category') or '—'}")
    add(f"器件   {rec.get('device') or '—'}    批次  {rec.get('batch') or '—'}")
    add(f"标签   {'、'.join(rec.get('tag_list') or []) or '—'}")
    add(f"目录   {rec.get('folder')}")
    _size = human_size(rec.get("total_bytes", 0))
    if rec.get("link_count"):
        _size += (f"（其中 {rec['link_count']} 个是链接，"
                  f"{human_size(rec['link_bytes'])} 不占额外空间）")
    add(f"文件   {rec.get('file_count', 0)} 个，{_size}")
    if rec.get("params"):
        add("参数   " + "  ".join(f"{k}={v}" for k, v in rec["params"].items()))
    for label, key in (("目的", "purpose"), ("条件", "conditions"),
                       ("结果", "results"), ("结论", "conclusion"), ("备注", "notes")):
        val = rec.get(key) or (load_meta(exp_dir(ctx, rec)) or {}).get(key, "")
        if val:
            add(f"{label}   {truncate(str(val).replace(chr(10), ' '), 90)}")

    files = ctx.db.files_of(rec["id"])
    if files:
        add("")
        add(hr())
        rows = [[truncate(f["relpath"], 56), human_size(f["size"]), f["role"],
                 "丢失" if f["missing"] else f["sha256"][:10]] for f in files]
        add(table(rows, ["文件", "大小", "类型", "哈希"], ["left", "right", "left", "left"]))
    events = ctx.db.events(rec["id"], limit=10)
    if events:
        add("")
        add(hr())
        for e in reversed(events):
            add(f"{human_time(e['ts'])}  {e['action']:<8}  {truncate(e['detail'], 70)}")
    return "\n".join(lines)


def search(ctx: Ctx, keyword: str) -> dict:
    kw = keyword.lower()
    recs = [
        r for r in ctx.db.all_experiments()
        if kw in json.dumps(r, ensure_ascii=False).lower()
    ]
    file_hits = [
        f for f in ctx.db.all_files()
        if kw in (f.get("relpath") or "").lower()
        or any(kw in tag.lower() for tag in f.get("tags", []))
    ]
    return {"experiments": recs, "files": file_hits}
