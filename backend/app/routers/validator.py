"""采集结果校验接口：维护校验规则，并围绕规则责任快照做批次重算与责任范围回读。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, PageResult
from app.services import rule_snapshot
from app.services.validation_scope import is_readonly
from app.services.validator import ValidatorService

router = APIRouter(prefix="/api/validator", tags=["采集结果校验"])

service = ValidatorService()

LIST_FIELDS = ["规则编号", "校验字段", "校验类型", "期望值", "容错范围", "采样比例", "阻断级别", "规则状态"]
STATUSES = ["草稿", "待测试", "已启用", "已停用"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


def team_of(request: Request) -> str | None:
    """责任范围从查询参数读取；缺省是平台视角（全部团队）。"""
    return request.query_params.get("team") or request.headers.get("X-Team")


def role_of(request: Request) -> str | None:
    return request.query_params.get("role") or request.headers.get("X-Role")


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待测试、已启用、已停用"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、规则编号关键字、状态与责任团队过滤采集结果校验列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status,
        page=page, size=size, team=team_of(request))
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/scope-options")
def scope_options() -> dict[str, object]:
    """责任范围下拉选项：团队清单与当前快照批次。"""
    teams = sorted({str(row.get("责任团队")) for row in rule_snapshot._rules() if row.get("责任团队")})
    snapshot = rule_snapshot.latest_snapshot()
    return {
        "teams": teams,
        "latest_batch": snapshot.get("批次号") if snapshot else None,
        "conflict_modes": ["按团队拆分", "共享规则合并"],
        "round_bases": ["权限隔离前置", "计算复用前置"],
    }


@router.get("/snapshot")
def get_snapshot(
    request: Request,
    batch: str | None = Query(default=None, description="批次号，缺省取最新批次"),
) -> dict[str, object]:
    """按责任范围回读规则责任快照；团队只拿到本归属的条目。"""
    return rule_snapshot.snapshot_view(
        team=team_of(request), role=role_of(request), batch_no=batch)


@router.get("/snapshot/batches")
def get_snapshot_batches() -> dict[str, object]:
    """按批次口径回查历史快照；原依据保留，不覆盖旧批次。"""
    return {"items": rule_snapshot.snapshot_batches()}


@router.post("/snapshot/rebuild")
def rebuild_snapshot(request: Request, payload: dict[str, object] | None = None) -> ActionResult:
    """重算规则责任快照：只读角色被拒绝，其余生成新批次并保留旧批次。"""
    values = payload or {}
    snapshot, message = rule_snapshot.build_snapshot(
        team=values.get("team") or team_of(request),
        role=values.get("role") or role_of(request),
        batch_note=str(values.get("remark") or ""),
    )
    if snapshot is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=snapshot)


@router.get("/ownership")
def get_ownership(request: Request) -> dict[str, object]:
    """校验页的规则归属视图：责任团队、共享授权、执行次数。"""
    team = team_of(request)
    rows = rule_snapshot.rule_ownership_rows(team=team)
    snapshot = rule_snapshot.latest_snapshot()
    return {
        "items": rows,
        "total": len(rows),
        "latest_batch": snapshot.get("批次号") if snapshot else None,
        "readonly": is_readonly(role_of(request)),
    }


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status,
        page=1, size=1000000, team=team_of(request))
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "责任团队", "共享授权", "采集源", "样本版本", "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS),
                  item.get("责任团队", ""), item.get("共享授权", ""),
                  item.get("采集源", ""), item.get("样本版本", ""), item.get("status", "")]
        row = []
        for value in values:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="validator.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int, request: Request) -> dict:
    """读取单条校验规则明细；越出责任范围按不存在处理，避免泄露其他团队规则。"""
    entry = service.get_entry(entry_id, team=team_of(request))
    if entry is None:
        raise HTTPException(status_code=404, detail=f"校验规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object], request: Request) -> ActionResult:
    """登记一条校验规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing, message = service.create_entry(
        payload, team=team_of(request), role=role_of(request))
    if message:
        return ActionResult(ok=False, message=message)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="校验规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload, request: Request) -> ActionResult:
    """执行提交测试、启用规则、停用规则；越权与只读角色都会被拦下并说明原因。"""
    entry, message = service.run_action(
        entry_id, payload.action.strip(), team=team_of(request), role=role_of(request))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
