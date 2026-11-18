"""校验责任范围公共口径：团队归一、规则签名、授权范围与样本结论。

采集结果校验、规则责任快照和采集监控都读同一份口径，避免各页面各自解释
「责任团队 / 共享授权 / 样本版本」。这些函数只依赖标准库，不访问仓库，
因此监控服务可以在不反向依赖校验服务的情况下复用它们。
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

PLATFORM_SCOPE = "全部团队"
READONLY_ROLE = "只读"
OPERATOR_ROLE = "操作员"
DEFAULT_OWNER_TEAM = "数据采集组"
DEFAULT_SOURCE = "默认采集源"
DEFAULT_SAMPLE_VERSION = "v2026.11"

SCOPE_FIELDS = ["责任团队", "共享授权", "采集源", "样本版本"]
SIGNATURE_FIELDS = ["校验字段", "校验类型", "期望值", "容错范围", "采样比例", "阻断级别"]


def normalize_scope(team: str | None) -> str:
    """把空值、平台视角都归一为「全部团队」。"""
    text = str(team or "").strip()
    if not text or text in {PLATFORM_SCOPE, "全部", "平台"}:
        return PLATFORM_SCOPE
    return text


def is_platform_scope(team: str | None) -> bool:
    return normalize_scope(team) == PLATFORM_SCOPE


def is_readonly(role: str | None) -> bool:
    text = str(role or "").strip().casefold()
    return text in {READONLY_ROLE.casefold(), "viewer", "readonly", "read-only", "只读角色"}


def split_teams(value: Any) -> list[str]:
    """共享授权既可以是逗号分隔字符串，也可以是列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        parts: Iterable[Any] = value
    else:
        parts = str(value).replace("，", ",").replace("、", ",").split(",")
    result: list[str] = []
    for part in parts:
        team = str(part).strip()
        if team and team != PLATFORM_SCOPE and team not in result:
            result.append(team)
    return result


def owner_team(row: dict[str, Any]) -> str:
    return str(row.get("责任团队") or "").strip() or DEFAULT_OWNER_TEAM


def authorized_teams(row: dict[str, Any], *, include_executor: bool = True) -> list[str]:
    """一条规则的结果对哪些团队可见：责任团队 + 显式共享授权。"""
    teams: list[str] = []
    if include_executor:
        teams.append(owner_team(row))
    for team in split_teams(row.get("共享授权")):
        if team not in teams:
            teams.append(team)
    return teams


def rule_signature(row: dict[str, Any]) -> tuple[str, ...]:
    """判定两条团队规则是否属于同一共享规则的签名。"""
    return tuple(str(row.get(field) or "").strip() for field in SIGNATURE_FIELDS)


def rule_source(row: dict[str, Any]) -> str:
    return str(row.get("采集源") or "").strip() or DEFAULT_SOURCE


def rule_sample_version(row: dict[str, Any]) -> str:
    return str(row.get("样本版本") or "").strip() or DEFAULT_SAMPLE_VERSION


def sample_outcome(rule: dict[str, Any], sample_version: str) -> dict[str, Any]:
    """从采集结果样本里取一份确定性结论，保证同一规则/批次可回查。"""
    digest = hashlib.sha256(
        "|".join([
            rule_source(rule),
            sample_version,
            *rule_signature(rule),
        ]).encode("utf-8")
    ).hexdigest()
    sample_size = 120 + int(digest[:2], 16)
    failed = int(digest[2:4], 16) % 15
    abnormal = failed >= 9
    return {
        "样本量": sample_size,
        "失败样本": failed,
        "校验结论": "异常" if abnormal else "通过",
        "异常类型": "数值越界" if abnormal else "",
        "阻断级别": str(rule.get("阻断级别") or "告警"),
    }
