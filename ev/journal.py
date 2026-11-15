"""Structured new journal entries alongside untouched legacy Markdown."""
import re
import uuid

from . import references
from .rich_text import scientific_html
from .util import human_time, now_iso


def make_entry(ctx, rec, text, author, files):
    return {"id": uuid.uuid4().hex, "text": text.rstrip(), "author": author,
            "created_at": now_iso(), "references": references.resolve(ctx, rec, files or [])}


def block(entry):
    who = "  ·  " + scientific_html(entry["author"].replace("\n", " ")) if entry.get("author") else ""
    links = references.markdown(entry.get("references", []))
    return (f"<!-- expvault-note:{entry['id']} -->\n### {human_time(entry['created_at'])}{who}\n\n"
            + scientific_html(entry["text"]) + ("\n\n引用资料：\n"+links if links else "")
            + f"\n<!-- /expvault-note:{entry['id']} -->")


def blocks(text, entries):
    known = {entry["id"]: entry for entry in entries}
    result, start = [], 0
    for match in re.finditer(r"<!-- expvault-note:([a-f0-9]{32}) -->.*?<!-- /expvault-note:\1 -->", text, re.S):
        entry = known.get(match[1])
        if not entry or match[0] != block(entry):
            continue  # Never hide a manually changed block or unknown Markdown.
        if text[start:match.start()].strip():
            result.append({"kind":"legacy", "text":text[start:match.start()].strip()})
        result.append({"kind":"entry", **entry})
        start = match.end()
    if text[start:].strip():
        result.append({"kind":"legacy", "text":text[start:].strip()})
    return result


def update_references(ctx, rec, note_id, files):
    from .nas import require_writer
    require_writer(ctx)
    from . import experiment as ex
    folder = ex.exp_dir(ctx, rec)
    path = folder / "04_notes/日志.md"
    if folder.resolve() not in path.resolve().parents or path.is_symlink():
        raise ValueError("日志路径越界")
    meta = ex.load_meta(folder)
    entries = meta.get("journal_entries", [])
    old = next((item for item in entries if item["id"] == note_id), None)
    if not old:
        raise ValueError("日志记录不存在，旧版日志可追加新的引用记录")
    updated = {**old, "references": references.resolve(ctx, rec, files), "updated_at":now_iso()}
    text = path.read_bytes().decode("utf-8")
    original = block(old)
    if text.count(original) != 1:
        raise ValueError("日志文本已在外部修改，请先核对原始日志；不会覆盖外部修改")
    temporary = path.with_suffix(".md.tmp")
    temporary.write_bytes(text.replace(original, block(updated), 1).encode("utf-8"))
    temporary.replace(path)
    entries[entries.index(old)] = updated
    meta["updated_at"] = updated["updated_at"]
    meta.setdefault("history", []).append({"ts":meta["updated_at"], "action":"note_references", "detail":f"调整日志 {note_id} 的资料引用"})
    ex.save_meta(folder, meta)
    ctx.db.log("note_references", rec["id"], f"调整日志 {note_id} 的资料引用")
    return updated
