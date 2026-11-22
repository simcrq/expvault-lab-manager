"""Deterministic three-way merge; undecidable edits retain both versions."""
from __future__ import annotations

import copy
import difflib
import hashlib
import json

MISSING = object()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def option(value):
    return {"deleted": True} if value is MISSING else {"value": value}


def text_merge(base, left, right):
    """Merge disjoint line edits; overlapping edits require a human decision."""
    original = base.splitlines(keepends=True)
    edits = []
    for text in (left, right):
        changed = text.splitlines(keepends=True)
        for tag, first, last, a, b in difflib.SequenceMatcher(None, original, changed, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            edit = (first, last, changed[a:b])
            if edit in edits:
                continue
            for start, end, _lines in edits:
                if (max(first, start) < min(last, end) or first == start
                        or (first == last and start < first < end)
                        or (start == end and first < start < last)):
                    return None
            edits.append(edit)
    result = list(original)
    for first, last, lines in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        result[first:last] = lines
    return "".join(result)


def merge_states(base, left, right):
    conflicts = {}
    for source in (base, left, right):
        for key, value in source.get("conflicts", {}).items():
            conflicts.setdefault(key, copy.deepcopy(value))
    for key in conflicts:
        b = base.get("conflicts", {}).get(key, {}).get("resolved")
        l = left.get("conflicts", {}).get(key, {}).get("resolved")
        r = right.get("conflicts", {}).get(key, {}).get("resolved")
        conflicts[key]["resolved"] = r if l == b else l if r == b or l == r else None

    def conflict(path, b, l, r):
        choices = sorted([option(l), option(r)], key=digest)
        key = digest({"path": path, "base": option(b), "choices": choices})
        conflicts.setdefault(key, {"id": key, "path": path, "base": option(b), "choices": choices,
                                   "resolved": None, "kind": "file" if len(path) == 4 and path[:1] == ["experiments"] and path[2] == "files" else "record"})
        # Preserve an existing file in a delete/edit conflict until explicitly resolved.
        if len(path) == 4 and path[0] == "experiments" and path[2] == "files" and (l is MISSING or r is MISSING):
            return copy.deepcopy(r if l is MISSING else l)
        chosen = choices[0]
        return MISSING if chosen.get("deleted") else copy.deepcopy(chosen["value"])

    def merge(b, l, r, path):
        if path and path[-1] == "number" and "steps" in path and type(l) is int and type(r) is int:
            return min(l,r)
        if len(path) == 2 and path[0] in ("experiments", "workspaces") and (l is MISSING or r is MISSING) and b is not MISSING:
            # Whole-experiment deletion is retained as a conflict, never silently propagated.
            kept = r if l is MISSING else l
            if kept is not MISSING:
                choices = sorted([option(l),option(r)],key=digest)
                key = digest({"path":path,"base":option(b),"choices":choices})
                if conflicts.get(key,{}).get("resolved") == digest(option(MISSING)):
                    return MISSING
                conflict(path, b, l, r)
                return copy.deepcopy(kept)
        if l == r:
            return copy.deepcopy(l) if l is not MISSING else MISSING
        if l == b:
            return copy.deepcopy(r) if r is not MISSING else MISSING
        if r == b:
            return copy.deepcopy(l) if l is not MISSING else MISSING
        if path and path[-1] in ("updated_at", "created_at") and isinstance(l, str) and isinstance(r, str):
            return max(l, r) if path[-1] == "updated_at" else min(l, r)
        # A filtered placeholder is never a deletion or a competing binary version.
        if len(path) == 4 and path[0] == "experiments" and path[2] == "files" and isinstance(l, dict) and isinstance(r, dict) and ("sha256" in l or "sha256" in r):
            if l.get("omitted"):
                return copy.deepcopy(r)
            if r.get("omitted"):
                return copy.deepcopy(l)
            if "sha256" in l and "sha256" in r:
                return conflict(path, b, l, r)
        if isinstance(l, dict) and isinstance(r, dict) and (isinstance(b, dict) or b is MISSING):
            old = b if isinstance(b, dict) else {}
            result = {}
            for key in sorted(set(old) | set(l) | set(r)):
                value = merge(old.get(key, MISSING), l.get(key, MISSING), r.get(key, MISSING), path + [key])
                if value is not MISSING:
                    result[key] = value
            return result
        if isinstance(l, list) and isinstance(r, list) and (isinstance(b, list) or b is MISSING):
            old = b if isinstance(b, list) else []
            field = path[-1] if path else ""
            identity = "sync_id" if field == "steps" else "id" if field in ("measurement_groups", "runs", "journal_entries", "process_conditions") else None
            if identity and all(isinstance(item, dict) and identity in item for item in old + l + r):
                value = merge({item[identity]:item for item in old}, {item[identity]:item for item in l}, {item[identity]:item for item in r}, path)
                return sorted(value.values(), key=lambda item: (item.get("created_at", item.get("measured_at", "")), item[identity]))
            if field in ("history", "tags", "groups", "images", "files", "references") or "file_tags" in path or "issues" in path:
                # Append-only history keeps every distinct event; other sets honor removals.
                maps = [{digest(item): item for item in values} for values in (old, l, r)]
                keys = set(maps[1]) | set(maps[2])
                if field != "history":
                    keys -= (set(maps[0]) - set(maps[1])) | (set(maps[0]) - set(maps[2]))
                return [copy.deepcopy((maps[1] | maps[2])[key]) for key in sorted(keys)]
        if isinstance(b, str) and isinstance(l, str) and isinstance(r, str) and "\n" in b + l + r:
            result = text_merge(b, l, r)
            if result is not None:
                return result
        return conflict(path, b, l, r)

    roots = []
    for source in (base, left, right):
        roots.append({key:value for key, value in source.items() if key != "conflicts"})
    result = merge(*roots, [])
    result["conflicts"] = conflicts
    # Concurrent reference additions must not point at a silently deleted source file.
    for record in list(result.get("experiments", {}).values()):
        for item in [*record["meta"].get("steps", []), *record["meta"].get("journal_entries", [])]:
            for ref in item.get("references", []):
                target, relative = ref.get("sync_exp"), ref.get("path")
                if not target or not relative:
                    continue
                candidates = [source.get("experiments", {}).get(target) for source in (base,left,right)]
                old_record = next((value for value in candidates if value), None)
                if target not in result["experiments"] and old_record:
                    conflict(["experiments",target], old_record, MISSING, old_record)
                    result["experiments"][target] = copy.deepcopy(old_record)
                target_record = result["experiments"].get(target)
                value = next((value["files"][relative] for value in candidates if value and relative in value["files"]), None)
                if target_record and relative not in target_record["files"] and value:
                    conflict(["experiments",target,"files",relative], value, MISSING, value)
                    target_record["files"][relative] = copy.deepcopy(value)
    return normalize_state(result)


def normalize_state(state):
    """Stable display IDs, step numbers and visible binary conflict copies."""
    state = copy.deepcopy(state)
    records = state.setdefault("experiments", {})
    collisions = {}
    for key, rec in records.items():
        collisions.setdefault((rec["workspace"], rec["original_id"]), []).append(key)
    for keys in collisions.values():
        for index, key in enumerate(sorted(keys)):
            rec = records[key]
            rec["display_id"] = rec["original_id"] + ("-S" + key.rsplit(":", 1)[-1][:12] if index else "")
    for rec in records.values():
        if "archived" in rec["meta"] or rec["meta"].get("status") == "archived":
            rec["meta"]["archived"] = int(rec["meta"].get("status") == "archived")
        steps = rec["meta"].get("steps", [])
        steps.sort(key=lambda item: (item.get("created_at", ""), item.get("sync_id", "")))
        for index, item in enumerate(steps, 1):
            item["number"] = index
    for item in state.setdefault("conflicts", {}).values():
        path = item["path"]
        if item.get("resolved") or item["kind"] != "file" or len(path) != 4 or path[0] != "experiments" or path[1] not in records:
            continue
        rec = records[path[1]]
        relative = path[3]
        from pathlib import PurePosixPath
        original = PurePosixPath(relative)
        for variant in [*item["choices"],item.get("base", {})]:
            value = variant.get("value", {})
            if not value.get("sha256"):
                continue
            stem = original.stem.encode("utf-8")[:100].decode("utf-8",errors="ignore")
            suffix = original.suffix.encode("utf-8")[:24].decode("utf-8",errors="ignore")
            name = stem + ".同步冲突-" + value["sha256"][:20] + suffix
            copy_path = str(original.parent / name)
            if copy_path in rec["files"] and rec["files"][copy_path].get("sha256") != value["sha256"]:
                copy_path = str(original.parent/(value["sha256"]+".同步冲突副本"+suffix))
            rec["files"][copy_path] = value
            rec["meta"].setdefault("file_tags", {}).setdefault(copy_path, ["同步冲突副本"])
    return state


def choose_conflict(state, conflict_id, choice):
    state = copy.deepcopy(state)
    conflict = state.get("conflicts", {}).get(conflict_id)
    if not conflict or conflict.get("resolved"):
        raise ValueError("冲突不存在或已处理，请刷新")
    if type(choice) is not int or choice not in (0, 1, 2):
        raise ValueError("请选择版本一或版本二")
    target = state
    for key in conflict["path"][:-1]:
        if isinstance(target, list):
            target = next((item for item in target if item.get("id", item.get("sync_id")) == key), MISSING)
        else:
            target = target.get(key, MISSING)
        if target is MISSING:
            if choice == 2:
                conflict["resolved"] = digest(option(MISSING))
                return normalize_state(state)
            raise ValueError("这处记录已被后续修改移除，请选择保留当前最新内容")
    key = conflict["path"][-1]
    current = next((item for item in target if item.get("id",item.get("sync_id")) == key),MISSING) if isinstance(target,list) else target.get(key,MISSING)
    if choice == 2:
        conflict["resolved"] = digest(option(current))
        return normalize_state(state)
    if option(current) not in conflict["choices"]:
        raise ValueError("这处内容已有后续修改，请重新比较，或选择保留当前最新内容")
    value = conflict["choices"][choice]
    if value.get("deleted"):
        if conflict["path"][:1] == ["workspaces"] and len(conflict["path"]) == 2 and any(record["workspace"] == key for record in state["experiments"].values()):
            raise ValueError("该工作空间仍有实验，请先整理实验，或保留当前工作空间")
        if isinstance(target, list):
            target[:] = [item for item in target if item.get("id", item.get("sync_id")) != key]
        else:
            target.pop(key, None)
    elif isinstance(target, list):
        index = next((index for index, item in enumerate(target) if item.get("id", item.get("sync_id")) == key), None)
        if index is None:
            target.append(value["value"])
        else:
            target[index] = value["value"]
    else:
        target[key] = value["value"]
    conflict["resolved"] = digest(value)
    return normalize_state(state)
