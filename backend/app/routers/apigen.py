"""API接口生成接口：维护接口配置，覆盖发布接口、废弃接口、删除接口等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.apigen import ApigenService
from app.services.datapack_build import build_service

router = APIRouter(prefix="/api/apigen", tags=["API接口生成"])

service = ApigenService()

LIST_FIELDS = ["接口编号", "接口名称", "请求方法", "请求路径", "参数定义", "响应模板", "超时秒数", "接口状态"]
STATUSES = ["草稿", "测试中", "已发布", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按接口编号检索"),
    status: str | None = Query(default=None, description="草稿、测试中、已发布、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按接口编号与状态过滤API接口生成列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 静态路径必须排在 /{entry_id} 之前：FastAPI 0.163 下路径参数转换失败会直接 422，
# 不会再回退匹配后续路由。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出API接口生成清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "apigen", "total": total, "items": items}


@router.get("/datapack-snapshots")
def list_datapack_snapshots() -> dict[str, Any]:
    """接口配置侧读取已发布的字段映射快照。

    快照在数据模型封装发布时冻结，这里与封装侧读取的是同一份对象，
    保证接口配置拿到的字段口径与已发布封装完全一致。
    """
    items = build_service.list_snapshots()
    items.reverse()
    return {"items": items, "total": len(items)}


@router.get("/datapack-snapshots/{snapshot_id}")
def get_datapack_snapshot(snapshot_id: str) -> dict[str, Any]:
    """读取单份冻结快照；未发布或不存在的快照不对外暴露。"""
    snapshot = build_service.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"字段映射快照 {snapshot_id} 不存在或尚未发布")
    return snapshot


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条接口配置明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"接口配置 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条接口配置，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="接口配置已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条接口配置执行发布接口、废弃接口、删除接口；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
