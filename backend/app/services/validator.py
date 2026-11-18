"""采集结果校验业务规则：状态流转、字段校验、责任范围与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.rule_snapshot import check_reuse_permission, latest_snapshot
from app.services.validation_scope import (
    DEFAULT_OWNER_TEAM,
    SCOPE_FIELDS,
    authorized_teams,
    is_platform_scope,
    is_readonly,
    normalize_scope,
)
from app.store import store

MODULE = "validator"
LIST_FIELDS = ["规则编号", "校验字段", "校验类型", "期望值", "容错范围", "采样比例", "阻断级别", "规则状态"]
REQUIRED_FIELDS = ["规则编号", "校验字段", "校验类型"]
STATUS_ORDER = ["草稿", "待测试", "已启用", "已停用"]
ACTION_RULES = {"提交测试": "待测试", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]


class ValidatorService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        team: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if not is_platform_scope(team):
            view_team = normalize_scope(team)
            # 只读角色与普通团队走同一口径：越出责任范围的规则不出现。
            rows = [row for row in rows if view_team in authorized_teams(row)]
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int, *, team: str | None = None) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        if not is_platform_scope(team) and normalize_scope(team) not in authorized_teams(entry):
            return None
        return entry

    def create_entry(
        self,
        values: dict[str, Any],
        *,
        team: str | None = None,
        role: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[str], str]:
        if is_readonly(role):
            return None, [], "只读角色不能登记或修改校验规则"
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        for field in SCOPE_FIELDS:
            entry[field] = values.get(field)
        if not str(entry.get("责任团队") or "").strip():
            # 平台视角登记且未写责任团队时落到默认团队，避免出现跨团队可见的空归属规则。
            entry["责任团队"] = DEFAULT_OWNER_TEAM if is_platform_scope(team) else normalize_scope(team)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, [], ""

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        team: str | None = None,
        role: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        if is_readonly(role):
            return None, "只读角色只能查看责任范围内的校验规则，不能改动规则责任快照"
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"校验规则 {entry_id} 不存在或已归档"
        allowed, reason = check_reuse_permission(entry_id, team=team)
        if not allowed:
            return None, reason
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集结果校验可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 规则归属变化后快照里的旧结论不再代表当前口径，标记为待重算批次。
        snapshot = latest_snapshot()
        if snapshot is not None:
            entry["快照待重算"] = True
        return entry, f"校验规则已{action}"
