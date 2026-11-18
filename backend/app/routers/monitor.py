"""采集监控 分析看板接口：汇总指标、参数运行、责任快照汇总与结果导出。"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.schemas import EntryPayload
from app.services.monitor import MonitorService

router = APIRouter(prefix="/api/monitor", tags=["采集监控"])
service = MonitorService()
LIST_FIELDS = ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"]
STATUSES = ["正常", "预警", "告警"]


def team_of(request: Request) -> str | None:
    return request.query_params.get("team") or request.headers.get("X-Team")


def batch_of(request: Request) -> str | None:
    return request.query_params.get("batch")


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return {"metrics": [
        {"label": "覆盖数据源", "value": random.randint(12, 45), "trend": random.randint(-12, 25)},
        {"label": "最新指标值", "value": round(random.uniform(0.72, 0.99), 3), "trend": random.randint(-8, 15)},
        {"label": "异常记录", "value": random.randint(0, 8), "trend": -random.randint(1, 10)},
    ]}


@router.get("")
def list_results() -> dict[str, Any]:
    return {"items": service.list_results()}


@router.get("/rule-snapshot")
def get_rule_snapshot(request: Request) -> dict[str, Any]:
    """按团队汇总规则责任快照里的校验异常；越权结果不计入趋势。"""
    return service.rule_snapshot_summary(team=team_of(request), batch_no=batch_of(request))


@router.get("/rule-snapshot/chain")
def get_service_chain(request: Request) -> dict[str, Any]:
    """按校验服务调用链回查监控服务记录：快照批次 -> 回读记录 -> 待处理清单。"""
    return service.service_chain(batch_no=batch_of(request), team=team_of(request))


@router.get("/rule-snapshot/pending")
def get_pending(request: Request) -> dict[str, Any]:
    """规则责任快照落到待处理清单的结果；重复提交只保留首份确认。"""
    items = service.pending_items(team=team_of(request))
    return {"items": items, "total": len(items)}


@router.get("/rule-snapshot/readbacks")
def get_read_backs(request: Request) -> dict[str, Any]:
    """协作业务面监控服务的可回读记录。"""
    items = service.read_back_records(team=team_of(request))
    return {"items": items, "total": len(items)}


@router.post("/rule-snapshot/sync")
def sync_rule_snapshot(request: Request) -> dict[str, Any]:
    """把最新快照同步到待处理清单与可回读记录，返回本次新增与重复命中数。"""
    return service.sync_team_rules(team=team_of(request))


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


@router.get("/export")
def export_entries(request: Request) -> StreamingResponse:
    items = service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=monitor.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"监控面板 {entry_id} 不存在")
    return entry
