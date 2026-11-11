"""文件标签的规范化；meta.json 的 file_tags 是权威数据。"""
from __future__ import annotations

import re
import unicodedata


def normalize_tags(value: str | list[str] | None) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,，;；\n]+", value)
    result = []
    for tag in value or []:
        tag = unicodedata.normalize("NFC", str(tag)).strip()
        if tag and tag not in result:
            if len(tag) > 80:
                raise ValueError("单个文件标签不能超过 80 个字符")
            if any(ord(c) < 32 for c in tag):
                raise ValueError("文件标签不能包含控制字符")
            result.append(tag)
    if len(result) > 50:
        raise ValueError("每个文件最多设置 50 个标签")
    return result
