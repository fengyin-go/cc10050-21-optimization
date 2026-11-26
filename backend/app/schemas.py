"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    # 归并批次等扩展参数允许透传，领域服务按需读取，路由层不做业务判断
    model_config = ConfigDict(extra="allow")

    action: str
    remark: str | None = None

    # 逐对合并的保留档案
    keep_id: int | str | None = None
    keep_code: str | None = None
    target_id: int | str | None = None
    target_code: str | None = None

    # 批量归并的合并组 / 合并对
    groups: list[dict[str, Any]] | None = None
    merges: list[dict[str, Any]] | None = None
    members: list[Any] | None = None
    codes: list[Any] | None = None

    # 处理单继续 / 幂等重提
    order_id: int | str | None = None
    batch_no: str | None = None
    submit_key: str | None = None
    only_groups: list[str] | None = None
    group_keys: list[str] | None = None
    group_key: str | None = None
    strategy: str | None = None
    force: bool | None = None
