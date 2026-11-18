"""规则责任快照：按责任范围复用校验结果，并核对团队读取权限。

设计取舍（按批次口径重算、原依据保留）：

1. 一次批次只执行一遍 —— 批次内按「采集源 + 样本版本 + 规则签名」归并，
   同一共享规则只算一次，结论写进快照条目；团队规则各自持有一条条目，
   但复用键相同，因此不会重复跑样本。
2. 责任边界优先 —— 默认按团队隔离结果，团队只能看到责任团队 / 共享授权
   命中的条目。跨团队复用必须写在共享授权里，越权规则不进入该团队视图。
3. 冲突口径 —— 同一采集源、同一样本版本下两个团队规则签名一致但责任团队
   不同（责任归属冲突）时，默认「按团队拆分、各自执行」；只有双方都把对方
   写进共享授权，才允许合并为一份复用结果（共享规则合并口径）。
4. 重算 —— 每次重算生成新的批次号，旧快照原样保留，便于按批次回查。
5. 只读角色 —— 只读角色只能回读自己责任范围内的快照，重算与动作都会被拒绝。
"""
from __future__ import annotations

from typing import Any

from app.services.validation_scope import (
    OPERATOR_ROLE,
    authorized_teams,
    is_platform_scope,
    is_readonly,
    normalize_scope,
    owner_team,
    rule_sample_version,
    rule_signature,
    rule_source,
    sample_outcome,
    split_teams,
)
from app.store import store

MODULE = "validator"
SNAPSHOT_TABLE = "rule_responsibility_snapshots"

CONFLICT_NONE = "无冲突"
CONFLICT_SPLIT = "按团队拆分"
CONFLICT_MERGE = "共享规则合并"
ROUND_PERMISSION = "权限隔离前置"
ROUND_REUSE = "计算复用前置"


def _snapshots() -> list[dict[str, Any]]:
    return store.aux_rows(SNAPSHOT_TABLE)


def _rules() -> list[dict[str, Any]]:
    return store.rows(MODULE)


def latest_snapshot() -> dict[str, Any] | None:
    snapshots = _snapshots()
    return snapshots[-1] if snapshots else None


def find_snapshot(batch_no: str | None = None) -> dict[str, Any] | None:
    if not batch_no:
        return latest_snapshot()
    return next((item for item in _snapshots() if item.get("批次号") == batch_no), None)


def _next_batch_no() -> str:
    return f"BATCH-{len(_snapshots()) + 1:04d}"


def _mutual_clusters(signature_rules: list[dict[str, Any]]) -> dict[str, str]:
    """把同签名规则按「互相授权」关系切成若干簇，返回 责任团队 -> 簇标识。

    只有彼此都把对方写进共享授权的团队才落在同一簇，允许合并成一份复用结论；
    其余团队即使签名、采集源、样本版本都相同，也只按自己的责任边界执行。
    """
    owners = sorted({owner_team(rule) for rule in signature_rules})
    grants: dict[str, set[str]] = {}
    for owner in owners:
        for rule in signature_rules:
            if owner_team(rule) == owner:
                grants.setdefault(owner, set()).update(split_teams(rule.get("共享授权")))
    label: dict[str, str] = {}
    seen: set[str] = set()
    for owner in owners:
        if owner in seen:
            continue
        # 在「互相授权」无向图上做连通分量，保证每个团队只落进一个簇。
        component: list[str] = []
        stack = [owner]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.append(current)
            stack.extend(
                other for other in owners
                if other not in component
                and other in grants.get(current, set())
                and current in grants.get(other, set())
            )
        seen.update(component)
        key = "+".join(sorted(component))
        for team in component:
            label[team] = key
    return label


def _scope_basis(owner: str, grants: list[str]) -> str:
    return f"{owner}+{'+'.join(grants)}" if grants else owner


def _group_key(rule: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    return (rule_source(rule), rule_sample_version(rule), rule_signature(rule))


def build_snapshot(*, team: str | None = None, role: str | None = None,
                   batch_note: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """生成一份新的规则责任快照；返回快照与可读说明。"""
    if is_readonly(role):
        return None, "只读角色不能重算规则责任快照"

    rules = list(_rules())
    if not rules:
        return None, "没有可执行的校验规则，未生成快照"

    groups: dict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = {}
    for rule in rules:
        groups.setdefault(_group_key(rule), []).append(rule)
    clusters = {key: _mutual_clusters(items) for key, items in groups.items()}

    batch_no = _next_batch_no()
    executions: dict[tuple[str, ...], dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    executed = 0
    reused = 0

    for rule in rules:
        key = _group_key(rule)
        same_group = groups[key]
        owners = {owner_team(item) for item in same_group}
        conflict = len(owners) > 1
        cluster = clusters[key].get(owner_team(rule), owner_team(rule))
        merged = conflict and "+" in cluster
        if merged:
            conflict_mode = CONFLICT_MERGE
            round_basis = ROUND_REUSE
            reuse_key: tuple[str, ...] = (*key, cluster)
        elif conflict:
            conflict_mode = CONFLICT_SPLIT
            round_basis = ROUND_PERMISSION
            reuse_key = (*key, owner_team(rule))
        else:
            conflict_mode = CONFLICT_NONE
            round_basis = ROUND_PERMISSION
            reuse_key = (*key, "*")

        if reuse_key in executions:
            reused += 1
            reuse_status = "复用"
            result = executions[reuse_key]
        else:
            executed += 1
            reuse_status = "执行"
            result = sample_outcome(rule, key[1])
            executions[reuse_key] = result

        owner = owner_team(rule)
        grants = [name for name in authorized_teams(rule) if name != owner]
        entries.append({
            "规则ID": int(rule.get("id", 0)),
            "规则编号": str(rule.get("规则编号") or ""),
            "责任团队": owner,
            "共享授权": ",".join(grants),
            "责任范围依据": _scope_basis(owner, grants),
            "采集源": key[0],
            "样本版本": key[1],
            "规则签名": "|".join(key[2]),
            "复用键": "::".join([key[0], key[1], "|".join(key[2]), reuse_key[-1]]),
            "冲突模式": conflict_mode,
            "本轮口径": round_basis,
            "复用结论": reuse_status,
            "执行次数": 1 if reuse_status == "执行" else 0,
            "样本科数": result["样本量"],
            "失败样本": result["失败样本"],
            "校验结论": result["校验结论"],
            "异常类型": result["异常类型"],
            "阻断级别": result["阻断级别"],
            "批次号": batch_no,
        })

    snapshot = {
        "id": len(_snapshots()) + 1,
        "批次号": batch_no,
        "生成视角": normalize_scope(team),
        "生成角色": str(role or OPERATOR_ROLE),
        "规则总数": len(rules),
        "执行规则数": executed,
        "复用规则数": reused,
        "共享规则组": sum(1 for items in groups.values() if len({owner_team(i) for i in items}) > 1),
        "合并复用组": sum(
            1 for items in groups.values()
            if len({owner_team(i) for i in items}) > 1
            and any("+" in label for label in _mutual_clusters(items).values())
        ),
        "异常条目": sum(1 for entry in entries if entry["校验结论"] == "异常"),
        "备注": str(batch_note or "").strip(),
        "条目": entries,
    }
    _snapshots().append(snapshot)
    return snapshot, f"规则责任快照 {batch_no} 已生成：执行 {executed} 条，复用 {reused} 条"


def _entry_in_scope(entry: dict[str, Any], team: str) -> bool:
    if entry.get("责任团队") == team:
        return True
    return team in set(split_teams(entry.get("共享授权")))


def visible_entries(snapshot: dict[str, Any], *, team: str | None) -> list[dict[str, Any]]:
    """团队只能读取本责任范围的条目；平台视角（未指定团队）才看全量。"""
    entries = list(snapshot.get("条目") or [])
    if is_platform_scope(team):
        return entries
    view_team = normalize_scope(team)
    return [entry for entry in entries if _entry_in_scope(entry, view_team)]


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "条目"}


def snapshot_view(*, team: str | None = None, role: str | None = None,
                  batch_no: str | None = None) -> dict[str, Any]:
    """回读快照：默认最新批次，返回当前团队可见条目与批次汇总。"""
    snapshot = find_snapshot(batch_no)
    if snapshot is None:
        return {"snapshot": None, "entries": [], "total": 0}
    entries = visible_entries(snapshot, team=team)
    return {
        "snapshot": _public_snapshot(snapshot),
        "entries": entries,
        "total": len(entries),
        "readonly": is_readonly(role),
    }


def check_reuse_permission(rule_id: int, *, team: str | None) -> tuple[bool, str]:
    """登记/动作前置核对：越出责任范围的规则不能借用其他团队结果。"""
    rule = next((item for item in _rules() if int(item.get("id", 0)) == rule_id), None)
    if rule is None:
        return False, f"校验规则 {rule_id} 不存在或已归档"
    if is_platform_scope(team):
        return True, "平台视角可核对全量规则"
    view_team = normalize_scope(team)
    if view_team in authorized_teams(rule):
        return True, f"团队 {view_team} 在规则责任范围内"
    return False, f"规则 {rule_id} 的责任归属为 {owner_team(rule)}，团队 {view_team} 不能复用该结果"


def rule_ownership_rows(*, team: str | None = None) -> list[dict[str, Any]]:
    """校验页的规则归属视图：责任团队、共享授权、执行次数。"""
    snapshot = latest_snapshot()
    entries = visible_entries(snapshot, team=team) if snapshot else []
    index = {int(entry["规则ID"]): entry for entry in entries}
    rows: list[dict[str, Any]] = []
    for rule in _rules():
        rule_id = int(rule.get("id", 0))
        if not is_platform_scope(team) and normalize_scope(team) not in authorized_teams(rule):
            continue
        snapshot_entry = index.get(rule_id)
        shared = [name for name in split_teams(rule.get("共享授权")) if name != owner_team(rule)]
        rows.append({
            "id": rule_id,
            "规则编号": rule.get("规则编号"),
            "责任团队": owner_team(rule),
            "共享授权": ",".join(shared) or "—",
            "责任范围依据": _scope_basis(owner_team(rule), shared),
            "采集源": rule_source(rule),
            "样本版本": rule_sample_version(rule),
            "执行次数": int(snapshot_entry.get("执行次数", 0)) if snapshot_entry else 0,
            "复用结论": snapshot_entry.get("复用结论", "未生成快照") if snapshot_entry else "未生成快照",
            "冲突模式": snapshot_entry.get("冲突模式", CONFLICT_NONE) if snapshot_entry else CONFLICT_NONE,
            "状态": rule.get("status"),
        })
    return rows


def snapshot_batches() -> list[dict[str, Any]]:
    return [_public_snapshot(snapshot) for snapshot in _snapshots()]
