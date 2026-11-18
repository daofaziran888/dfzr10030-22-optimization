"""采集监控 分析看板服务：汇总指标、运行分析并生成模拟结果。

新增的规则责任汇总从规则责任快照读取：只统计当前团队授权范围内的条目，
越权结论不计入异常趋势；同时把快照落到待处理清单，并留一份可回读记录。
"""
from __future__ import annotations

import random
from typing import Any

from app.services import rule_snapshot
from app.services.validation_scope import is_platform_scope, normalize_scope
from app.store import store

MODULE = "monitor"
REQUIRED_FIELDS = ["面板编号", "采集源总数", "今日采集量"]
STATUS_ORDER = ["正常", "预警", "告警"]
ACTION_RULES = {"查看详情": "正常"}
NEGATIVE_ACTIONS = []
PENDING_TABLE = "validator_pending_list"
READ_BACK_TABLE = "rule_snapshot_readbacks"


def _pending_rows() -> list[dict[str, Any]]:
    return store.aux_rows(PENDING_TABLE)


def _snapshot_pending(snapshot: dict[str, Any], *, team: str | None) -> list[dict[str, Any]]:
    """把快照条目同步成待处理清单；重复提交同一批次只保留首份确认。

    团队视角只同步本责任范围的异常条目，平台视角按条目责任团队分别落清单。
    """
    existing = _pending_rows()
    created: list[dict[str, Any]] = []
    visible = rule_snapshot.visible_entries(snapshot, team=team)
    for entry in visible:
        if entry.get("校验结论") != "异常":
            continue
        batch_no = str(entry.get("批次号"))
        rule_id = int(entry.get("规则ID", 0))
        if any(int(row.get("规则ID", 0)) == rule_id and row.get("批次号") == batch_no
               for row in existing):
            continue
        row = {
            "id": len(existing) + len(created) + 1,
            "批次号": batch_no,
            "规则ID": rule_id,
            "规则编号": entry.get("规则编号"),
            "责任团队": entry.get("责任团队"),
            "共享授权": entry.get("共享授权"),
            "采集源": entry.get("采集源"),
            "样本版本": entry.get("样本版本"),
            "异常类型": entry.get("异常类型"),
            "阻断级别": entry.get("阻断级别"),
            # 平台视角同步时按条目自己的责任团队落清单，避免待处理项被记到「全部团队」
            # 而让具体团队查不到；团队视角同步只落本团队负责的条目。
            "同步团队": normalize_scope(team) if not is_platform_scope(team)
            else str(entry.get("责任团队") or "未归属"),
            "处理状态": "待处理",
            "读取次数": 1,
        }
        created.append(row)
    existing.extend(created)
    return existing


def _read_back(snapshot: dict[str, Any], *, team: str | None, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """协作业务面监控服务的可回读记录：同一批次重复同步时覆盖为最新一次。"""
    records = store.aux_rows(READ_BACK_TABLE)
    batch_no = str(snapshot.get("批次号"))
    view_team = normalize_scope(team)
    record = {
        "id": len(records) + 1,
        "批次号": batch_no,
        "读取团队": view_team,
        "读取方": "协作业务面监控服务",
        "条目数": len(entries),
        "异常条目": sum(1 for item in entries if item.get("校验结论") == "异常"),
        "记录时间": "2026-11-18",
    }
    existing = next(
        (item for item in records
         if item.get("批次号") == batch_no and item.get("读取团队") == view_team),
        None,
    )
    if existing is not None:
        record["id"] = existing["id"]
        record["读取次数"] = int(existing.get("读取次数", 1)) + 1
        existing.update(record)
        return existing
    record["读取次数"] = 1
    records.append(record)
    return record


class MonitorService:
    def list_results(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        results: list[dict] = []
        for row in rows:
            d = dict(row)
            d["指标值"] = round(random.uniform(0.65, 0.98), 3)
            d["对比基准"] = round(random.uniform(0.60, 0.92), 3)
            d["差异"] = round(d["指标值"] - d["对比基准"], 3)
            results.append(d)
        return results

    def run_analysis(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        count = int(params.get("样本范围", params.get("并行数量", "6")))
        results: list[dict] = []
        for i in range(count):
            results.append({
                "id": i + 1,
                "分组": f"分组{(i%3)+1}",
                "指标值": round(random.uniform(0.62, 1.02), 3),
                "对比基准": round(random.uniform(0.58, 0.95), 3),
                "差异": round(random.uniform(-0.2, 0.3), 3),
            })
        return results

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def rule_snapshot_summary(self, *, team: str | None = None,
                              batch_no: str | None = None) -> dict[str, Any]:
        """按责任快照汇总各团队校验异常；越权条目不计入趋势。

        快照由校验服务写入持久化仓库，监控服务按批次号回查同一份依据。
        """
        snapshot = rule_snapshot.find_snapshot(batch_no)
        if snapshot is None:
            return {"snapshot": None, "teams": [], "trend": [], "pending": len(_pending_rows()), "read_back": None}
        entries = rule_snapshot.visible_entries(snapshot, team=team)
        buckets: dict[str, dict[str, Any]] = {}
        for entry in entries:
            name = str(entry.get("责任团队") or "未归属")
            bucket = buckets.setdefault(name, {
                "团队": name, "规则数": 0, "异常数": 0, "复用数": 0, "执行次数": 0,
            })
            bucket["规则数"] += 1
            bucket["执行次数"] += int(entry.get("执行次数", 0))
            if entry.get("校验结论") == "异常":
                bucket["异常数"] += 1
            if entry.get("复用结论") == "复用":
                bucket["复用数"] += 1
        pending = _snapshot_pending(snapshot, team=team)
        read_back = _read_back(snapshot, team=team, entries=entries)
        return {
            "snapshot": {key: value for key, value in snapshot.items() if key != "条目"},
            "teams": sorted(buckets.values(), key=lambda item: item["团队"]),
            "trend": sorted(buckets.values(), key=lambda item: item["团队"]),
            "pending": sum(1 for row in pending if row.get("处理状态") == "待处理"
                           and (is_platform_scope(team) or row.get("同步团队") == normalize_scope(team))),
            "read_back": read_back,
        }

    def service_chain(self, *, batch_no: str | None = None, team: str | None = None) -> dict[str, Any]:
        """按校验服务调用链回查监控记录：校验服务 -> 快照 -> 监控回读 -> 待处理。"""
        snapshot = rule_snapshot.find_snapshot(batch_no)
        if snapshot is None:
            return {"batch": None, "links": [], "records": []}
        batch = str(snapshot.get("批次号"))
        chain_team = normalize_scope(team)
        links = [
            {"环节": "采集结果校验服务", "动作": "生成规则责任快照", "批次号": batch},
            {"环节": "规则责任快照仓库", "动作": "持久化批次条目", "条目数": len(snapshot.get("条目") or [])},
            {"环节": "协作业务面监控服务", "动作": "读取快照并留可回读记录", "批次号": batch},
            {"环节": "采集监控服务", "动作": "汇总团队异常趋势", "批次号": batch},
            {"环节": "待处理清单", "动作": "同步异常条目", "批次号": batch},
        ]
        records = {
            "read_backs": [row for row in store.aux_rows(READ_BACK_TABLE) if row.get("批次号") == batch],
            "pending": [row for row in _pending_rows() if row.get("批次号") == batch],
        }
        if not is_platform_scope(team):
            records = {
                key: [row for row in rows if row.get("读取团队") == chain_team
                      or row.get("同步团队") == chain_team or row.get("责任团队") == chain_team]
                for key, rows in records.items()
            }
        return {"batch": batch, "team": chain_team, "links": links, "records": records}

    def pending_items(self, *, team: str | None = None) -> list[dict[str, Any]]:
        rows = _pending_rows()
        if not is_platform_scope(team):
            view_team = normalize_scope(team)
            rows = [row for row in rows if row.get("同步团队") == view_team]
        return sorted(rows, key=lambda row: (str(row.get("批次号")), int(row.get("规则ID", 0))))

    def read_back_records(self, *, team: str | None = None) -> list[dict[str, Any]]:
        rows = store.aux_rows(READ_BACK_TABLE)
        if not is_platform_scope(team):
            view_team = normalize_scope(team)
            rows = [row for row in rows if row.get("读取团队") == view_team]
        return list(rows)

    def sync_team_rules(self, *, team: str | None = None) -> dict[str, Any]:
        """团队维度重新同步：同一批次只保留首份待处理确认。"""
        snapshot = rule_snapshot.latest_snapshot()
        if snapshot is None:
            return {"synced": 0, "duplicates": 0, "batch": None}
        entries = rule_snapshot.visible_entries(snapshot, team=team)
        abnormal = sum(1 for entry in entries if entry.get("校验结论") == "异常")
        before = len(_pending_rows())
        _snapshot_pending(snapshot, team=team)
        after = len(_pending_rows())
        created = after - before
        return {
            "synced": created,
            "duplicates": max(abnormal - created, 0),
            "batch": snapshot.get("批次号"),
        }
