"""Instrument-scoped repeated measurements stored in authoritative experiment metadata."""
from copy import deepcopy
from datetime import datetime

from jsonschema import Draft202012Validator, FormatChecker

from .rich_text import scientific_html

TEXT = {"type": "string", "maxLength": 256}
NAME = {**TEXT, "minLength": 1, "pattern": r"\S"}
ID = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
PARAMS = {"type": "object", "minProperties": 1, "maxProperties": 100,
          "propertyNames": {"minLength": 1, "maxLength": 128, "pattern": r"\S"},
          "additionalProperties": {"type": ["string", "number", "boolean", "null"], "maxLength": 2000}}
RUN = {"type": "object", "additionalProperties": False,
       "required": ["id", "name", "measured_at", "operator", "params"],
       "properties": {"id": ID, "name": NAME, "measured_at": {"type": "string", "format": "date-time", "pattern": r"^\d{4}-\d{2}-\d{2}T"},
                      "operator": TEXT, "params": PARAMS}}
GROUPS_SCHEMA = {"type": "array", "maxItems": 20,
                 "items": {"type": "object", "additionalProperties": False,
                           "required": ["id", "name", "model", "runs"],
                           "properties": {"id": ID, "name": NAME, "model": TEXT,
                                          "runs": {"type": "array", "maxItems": 50, "items": RUN}}}}
VALIDATOR = Draft202012Validator(GROUPS_SCHEMA, format_checker=FormatChecker())


def normalize(groups):
    if not VALIDATOR.is_valid(groups):
        raise ValueError("仪器或测量记录格式无效：最多 20 台仪器，每台 50 组测量，每组 1–100 项参数")
    result = deepcopy(groups)
    seen = set()
    for group in result:
        for item in [group, *group["runs"]]:
            if item["id"] in seen:
                raise ValueError("仪器和测量记录编号不能重复")
            seen.add(item["id"])
            item["name"] = item["name"].strip()
        group["model"] = group["model"].strip()
        for run in group["runs"]:
            try:
                measured = datetime.fromisoformat(run["measured_at"].replace("Z", "+00:00"))
                if measured.tzinfo is None or measured.utcoffset() is None:
                    raise ValueError
            except ValueError:
                raise ValueError("测量时间必须是含时区的有效日期时间") from None
    return result


def comparison_html(groups):
    """Safe, offline-readable comparison tables for README and HTML reports."""
    output = []
    for group in groups or []:
        output.append(f"<h3>{scientific_html(group['name'])}</h3><p>仪器型号：{scientific_html(group['model'] or '未记录')}</p>")
        runs = group["runs"]
        if not runs:
            output.append("<p>暂无测量记录</p>")
            continue
        headers = "".join(f"<th>{scientific_html(run['name'])}<br>{scientific_html(run['measured_at'])}<br>{scientific_html(run['operator'])}</th>" for run in runs)
        keys = dict.fromkeys(key for run in runs for key in run["params"])
        rows = "".join("<tr><th>" + scientific_html(key) + "</th>" + "".join(
            "<td>" + scientific_html(run["params"][key] if key in run["params"] else "—") + "</td>" for run in runs) + "</tr>" for key in keys)
        output.append(f'<div style="overflow:auto"><table><thead><tr><th>参数</th>{headers}</tr></thead><tbody>{rows}</tbody></table></div>')
    return "\n".join(output)
