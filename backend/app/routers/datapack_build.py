"""数据模型封装批量构建接口。

覆盖：原始数据源目录、多源一次提交的批量构建、构建结果查询、
发布冻结字段映射快照、失败数据源单独重新提交、废弃待发布构建。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.datapack_build import build_service

router = APIRouter(prefix="/api/datapack/builds", tags=["数据模型封装-批量构建"])


class SourceMapping(BaseModel):
    source: str
    mappings: list[dict[str, Any]] | None = None


class BuildPayload(BaseModel):
    name: str | None = None
    sources: list[SourceMapping] = Field(default_factory=list)


class RetryPayload(BaseModel):
    source: str
    mappings: list[dict[str, Any]] | None = None


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """数据源目录：批量构建页的多选项与默认字段映射都从这里读取。"""
    return {"items": build_service.list_sources()}


@router.get("")
def list_builds() -> dict[str, Any]:
    """构建记录列表：按提交时间倒序，最新一次构建在最前。"""
    items = build_service.list_builds()
    items.reverse()
    return {"items": items, "total": len(items)}


@router.post("")
def submit_build(payload: BuildPayload) -> dict[str, Any]:
    """多选原始数据源一次提交：整组校验并逐条返回每个源的处理结果。"""
    try:
        return build_service.submit_build(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{build_id}")
def get_build(build_id: int) -> dict[str, Any]:
    """构建结果明细：每个源的处理情况、最终输出结构、冲突项与映射快照。"""
    build = build_service.get_build(build_id)
    if build is None:
        raise HTTPException(status_code=404, detail=f"构建记录 {build_id} 不存在")
    return build


@router.post("/{build_id}/retry")
def retry_source(build_id: int, payload: RetryPayload) -> dict[str, Any]:
    """对单条失败数据源重新提交映射，不影响同批次其他源。"""
    try:
        return build_service.retry_source(build_id, payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{build_id}/publish")
def publish_build(build_id: int) -> dict[str, Any]:
    """发布构建：冻结字段映射快照，接口配置侧随后可读取同一份快照。"""
    try:
        return build_service.publish(build_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{build_id}/discard")
def discard_build(build_id: int) -> dict[str, Any]:
    """废弃仍在待发布的构建；已发布快照受冻结保护，不能废弃。"""
    try:
        return build_service.discard(build_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
