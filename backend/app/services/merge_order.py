"""分组处理单资源服务：让归并处理单也能走通用资源路由查询 / 动作。

真正的批次逻辑都在 ``GoodsRegistryService``，这里只做资源适配，
保证 ``/api/merge_order``、``/api/goods_merge_order`` 等只读列表、
详情和“继续处理”动作与专用批次接口看到的是同一份数据。
"""
from __future__ import annotations

from typing import Any

from app.merge_support import CONTINUE_ACTIONS, ORDER_TABLE, find_order
from app.services.base import BaseModuleService
from app.services.goods_registry import GoodsRegistryService
from app.store import store

_goods_service = GoodsRegistryService()


class MergeOrderService(BaseModuleService):
    MODULE = ORDER_TABLE
    ENTITY = "归并分组处理单"
    LIST_FIELDS = ["单号", "title", "operator", "status"]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = ["待处理", "部分办结", "等候处理", "已办结"]
    ACTION_RULES = {
        "继续处理": "部分办结",
        "继续原批": "部分办结",
        "重新提交": "部分办结",
    }
    NEGATIVE_ACTIONS: list[str] = []

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(_goods_service.list_orders())
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if keyword:
            token = keyword.casefold()
            rows = [
                row for row in rows
                if token in str(row.get("单号", "")).casefold()
                or token in str(row.get("batch_no", "")).casefold()
                or token in str(row.get("title", "")).casefold()
            ]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return _goods_service.get_order(entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        result = _goods_service.submit_batch(values or {})
        if not result.get("ok"):
            return None, [result.get("message", "批量归并单提交失败")]
        return result.get("order"), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        payload: Any | None = None,
    ) -> dict[str, Any] | tuple[dict[str, Any] | None, str]:
        data: dict[str, Any] = {}
        if payload is not None and hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_none=True)
            data.update(getattr(payload, "model_extra", None) or {})
        elif isinstance(payload, dict):
            data = payload
        if action in CONTINUE_ACTIONS or action in self.ACTION_RULES:
            return _goods_service.continue_order(entry_id, data)
        order = find_order(entry_id)
        if order is None:
            return None, f"处理单 {entry_id} 不存在"
        return super().run_action(entry_id, action, payload)
