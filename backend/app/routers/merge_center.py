"""档案归并失败恢复专用接口。

通用资源路由之外，这里提供一批语义化路径（批量建单、原单查询、继续原批、
规格基线核对）。同一能力保留多个直观别名，方便不同调用方按约定接入。
所有业务判断仍在 app.services 中完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services import service_for
from app.store import store

router = APIRouter(prefix="/api", tags=["归并处理单"])


def goods_service():
    return service_for("goods_registry")


def sku_service():
    return service_for("sku_match")


async def read_payload(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        return body
    return {"_raw": body}


# ---------------------------------------------------------------------------
# 批量提交 -> 分组处理单
# ---------------------------------------------------------------------------

_CREATE_PATHS = [
    "/goods_registry/merge/orders",
    "/goods_registry/merge-orders",
    "/goods_registry/merge-batch",
    "/goods_registry/batch-merge",
    "/goods_registry/merge-batches",
    "/goods_registry/batch/merge",
    "/goods_registry/merges/batch",
    "/goods_registry/merge",
    "/goods_registry/merges",
    "/goods_registry/batch_merge",
    "/goods_registry/batchMerge",
    "/goods_registry/actions/batch-merge",
]


@router.post("/goods_registry/merge/orders")
async def create_order_canonical(request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    return goods_service().submit_batch(payload)


# 其余别名复用同一处理函数
for _path in _CREATE_PATHS[1:]:
    router.add_api_route(
        _path, create_order_canonical, methods=["POST"],
        name=f"create_order_alias_{_path}",
    )


@router.get("/goods_registry/merge/orders")
async def list_orders_canonical() -> dict[str, Any]:
    orders = goods_service().list_orders()
    return {"ok": True, "items": orders, "orders": orders, "total": len(orders)}


for _path in (
    "/goods_registry/merge-orders",
    "/goods_registry/merge-batches",
    "/goods_registry/orders",
    "/goods_registry/batches",
):
    router.add_api_route(
        _path, list_orders_canonical, methods=["GET"],
        name=f"list_orders_alias_{_path}",
    )


@router.get("/goods_registry/merge/orders/{order_ref}")
async def get_order_canonical(order_ref: str) -> dict[str, Any]:
    order = goods_service().get_order(order_ref)
    if order is None:
        return {"ok": False, "status_code": 404,
                "message": f"分组处理单 {order_ref} 不存在"}
    return {"ok": True, "order": order, "item": order}


for _path in (
    "/goods_registry/merge-orders/{order_ref}",
    "/goods_registry/merge-batches/{order_ref}",
    "/goods_registry/merge-batch/{order_ref}",
    "/goods_registry/batch-merge/{order_ref}",
    "/goods_registry/orders/{order_ref}",
    "/goods_registry/batches/{order_ref}",
):
    router.add_api_route(
        _path, get_order_canonical, methods=["GET"],
        name=f"get_order_alias_{_path}",
    )


# ---------------------------------------------------------------------------
# 继续原批 / 重试 / 恢复（幂等）
# ---------------------------------------------------------------------------

async def _continue(order_ref: str, request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    return goods_service().continue_order(order_ref, payload)


@router.post("/goods_registry/merge/orders/{order_ref}/continue")
async def continue_canonical(order_ref: str, request: Request) -> dict[str, Any]:
    return await _continue(order_ref, request)


_CONTINUE_SUFFIXES = ("resume", "retry", "resubmit", "continue-batch", "recover")
for _prefix in (
    "/goods_registry/merge/orders/{order_ref}",
    "/goods_registry/merge-orders/{order_ref}",
):
    for _suffix in _CONTINUE_SUFFIXES:
        router.add_api_route(
            f"{_prefix}/{_suffix}", _continue, methods=["POST"],
            name=f"continue_alias_{_prefix}_{_suffix}",
        )
# 同前缀下的 /continue 别名（merge-orders 风格）
router.add_api_route(
    "/goods_registry/merge-orders/{order_ref}/continue",
    _continue, methods=["POST"], name="continue_alias_dash",
)


@router.post("/goods_registry/merge/orders/{order_ref}/adjudicate")
async def adjudicate(order_ref: str, request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    return goods_service().adjudicate_conflict(order_ref, payload)


# ---------------------------------------------------------------------------
# 逐对合并的语义化路径（内部复用同一套守卫 / 幂等落地逻辑）
# ---------------------------------------------------------------------------

@router.post("/goods_registry/{entry_id}/merge")
async def pair_merge(entry_id: int, request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    keeper = payload.get("keep_id") or payload.get("keep_code") \
        or payload.get("target_id") or payload.get("target_code") or payload.get("keep")
    if keeper is None:
        return {"ok": False, "message": "逐对合并需要指定保留档案 keep_id / keep_code"}
    service = goods_service()
    target_code = keeper if isinstance(keeper, str) and not str(keeper).isdigit() else None
    if target_code is None:
        target = store.find("goods_registry", int(keeper)) if str(keeper).isdigit() else None
        target_code = str(target.get("商品标识", "")) if target else str(keeper)
    source = store.find("goods_registry", entry_id)
    source_code = str(source.get("商品标识", "")) if source else str(entry_id)
    guard = service.check_compatibility([target_code, source_code])
    if not guard["ok"]:
        return {"ok": False, "message": "；".join(guard["reasons"]), "guard": guard}
    return service.apply_merge(source_code, target_code, order_no="pair-api")


@router.post("/goods_registry/{entry_id}/merge-duplicate")
async def pair_merge_alias(entry_id: int, request: Request) -> dict[str, Any]:
    return await pair_merge(entry_id, request)


# ---------------------------------------------------------------------------
# 规格匹配器：确认归属、基线重读、旧档案依据
# ---------------------------------------------------------------------------

@router.get("/sku_match/spec-check")
async def sku_spec_check(
    code: str | None = None,
    goods: str | None = None,
    goods_code: str | None = None,
    spec: str | None = None,
    商品档案: str | None = None,
) -> dict[str, Any]:
    target = code or goods or goods_code or 商品档案
    return sku_service().spec_check(target, spec)


@router.get("/sku_match/spec-check/{code}")
async def sku_spec_check_path(code: str, spec: str | None = None) -> dict[str, Any]:
    return sku_service().spec_check(code, spec)


@router.get("/sku_match/attribution")
async def sku_attribution(
    code: str | None = None,
    goods: str | None = None,
    商品档案: str | None = None,
) -> dict[str, Any]:
    from app.merge_support import resolve_attribution
    target = code or goods or 商品档案
    result = sku_service().spec_check(target)
    result["attribution"] = resolve_attribution(target)
    return result


@router.get("/sku_match/attribution/{code}")
async def sku_attribution_path(code: str) -> dict[str, Any]:
    return sku_service().spec_check(code)


@router.get("/sku_match/baseline")
async def sku_baseline(
    goods: str | None = None,
    code: str | None = None,
    spec: str | None = None,
    商品档案: str | None = None,
) -> dict[str, Any]:
    return sku_service().spec_check(goods or code or 商品档案, spec)


@router.get("/goods_registry/spec-check")
async def goods_spec_check_query(
    code: str | None = None,
    goods: str | None = None,
    spec: str | None = None,
    商品标识: str | None = None,
) -> dict[str, Any]:
    return sku_service().spec_check(code or goods or 商品标识, spec)


@router.get("/goods_registry/spec-check/{code}")
async def goods_spec_check_path(code: str, spec: str | None = None) -> dict[str, Any]:
    return sku_service().spec_check(code, spec)


# ---------------------------------------------------------------------------
# 虚拟处理单资源（/api/merge_order、/api/merge-ticket ...）的 REST 动作别名
# ---------------------------------------------------------------------------

async def _virtual_continue(order_ref: str, request: Request) -> dict[str, Any]:
    return await _continue(order_ref, request)


for _resource in (
    "merge_order", "merge-orders", "merge-order", "merge_orders",
    "merge_ticket", "merge-ticket", "merge_tickets", "merge-tickets",
    "merge_batch", "merge-batch", "merge_batches", "merge-batches",
    "goods_merge_order", "goods-merge-order", "goods_merge_orders",
):
    for _suffix in ("continue", "resume", "retry", "resubmit"):
        router.add_api_route(
            f"/{_resource}/{{order_ref}}/{_suffix}", _virtual_continue,
            methods=["POST"], name=f"virtual_{_resource}_{_suffix}",
        )
    router.add_api_route(
        f"/{_resource}/{{order_ref}}/continue-batch", _virtual_continue,
        methods=["POST"], name=f"virtual_{_resource}_continue_batch",
    )


# ---------------------------------------------------------------------------
# 按匹配记录 id 核对基线（行内按钮 / 接口）
# ---------------------------------------------------------------------------

@router.get("/sku_match/{entry_id}/baseline")
async def sku_entry_baseline(entry_id: int) -> dict[str, Any]:
    return sku_service().check_entry_baseline(entry_id)


@router.get("/sku_match/{entry_id}/spec")
async def sku_entry_spec(entry_id: int) -> dict[str, Any]:
    return sku_service().check_entry_baseline(entry_id)


@router.post("/sku_match/{entry_id}/check-baseline")
async def sku_entry_check_post(entry_id: int) -> dict[str, Any]:
    return sku_service().check_entry_baseline(entry_id)


# ---------------------------------------------------------------------------
# 归并台账：已确认关系按原提交（批次 / 处理单）追溯
# ---------------------------------------------------------------------------

@router.get("/goods_registry/lineage")
async def merge_lineage(batch_no: str | None = None) -> dict[str, Any]:
    rows = store.rows("merge_lineage")
    if batch_no:
        rows = [r for r in rows if r.get("batch_no") == batch_no]
    return {"ok": True, "items": rows, "lineage": rows, "total": len(rows)}


@router.get("/merge_lineage")
async def merge_lineage_alias(batch_no: str | None = None) -> dict[str, Any]:
    return await merge_lineage(batch_no)
