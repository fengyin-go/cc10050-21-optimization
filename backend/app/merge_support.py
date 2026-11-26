"""档案归并的共享领域工具：标识解析、规格归一、归属链追溯。

商品档案（goods_registry）在归并后不会被物理删除：被并档案保留行并写入
``并入档案``，同时在 ``merge_lineage`` 留一条已确认关系。规格匹配器与价格
基线通过本模块解析“当前归属”和“之前所属档案”，保证基线重读与历史追溯
使用同一套口径。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

GOODS_MODULE = "goods_registry"
ORDER_TABLE = "merge_order"
LINEAGE_TABLE = "merge_lineage"

# 归并处理单 / 合并组状态
GROUP_PENDING = "待处理"
GROUP_DONE = "已办结"
GROUP_WAITING = "等候处理"

ORDER_PENDING = "待处理"
ORDER_PARTIAL = "部分办结"
ORDER_DONE = "已办结"
ORDER_WAITING = "等候处理"

CONTINUE_ACTIONS = {
    "继续处理", "继续归并", "继续原批", "继续原批提交", "继续提交",
    "重新提交", "重新提交该批", "重试合格组", "恢复处理",
    "continue", "resume", "retry", "resubmit",
}

BATCH_ACTIONS = {
    "批量合并", "批量归并", "批量提交归并", "提交批量归并", "批量合并档案",
    "提交合并组", "合并批次", "batch_merge", "merge_batch", "batch-merge",
}

KEEPER_KEYS = (
    "keep", "keeper", "target", "keep_code", "keep_id", "target_code",
    "target_id", "保留档案", "保留商品", "主档案",
)
MEMBER_KEYS = (
    "members", "member_codes", "member_ids", "sources", "merge_codes",
    "merge_ids", "items", "并入档案", "合并档案", "被并档案",
)


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def goods_rows() -> list[dict[str, Any]]:
    return store.rows(GOODS_MODULE)


def find_goods(code_or_id: Any) -> dict[str, Any] | None:
    """按商品标识（G42）或数字 id / 数字字符串找档案。"""
    if code_or_id is None:
        return None
    if isinstance(code_or_id, int):
        by_id = store.find(GOODS_MODULE, code_or_id)
        if by_id is not None:
            return by_id
    token = str(code_or_id).strip()
    if not token:
        return None
    if token.isdigit():
        by_id = store.find(GOODS_MODULE, int(token))
        if by_id is not None:
            return by_id
    for row in goods_rows():
        if str(row.get("商品标识", "")).strip() == token:
            return row
    # 容忍调用方传入小写 / 带空格的标识
    folded = token.casefold()
    for row in goods_rows():
        if str(row.get("商品标识", "")).strip().casefold() == folded:
            return row
    return None


def to_code(code_or_id: Any) -> str | None:
    """把 id / 标识 / 档案对象统一成商品标识字符串。"""
    if code_or_id is None:
        return None
    if isinstance(code_or_id, dict):
        return str(code_or_id.get("商品标识", "") or "") or None
    goods = find_goods(code_or_id)
    if goods is not None:
        return str(goods.get("商品标识", ""))
    token = str(code_or_id).strip()
    return token or None


def normalize_spec(spec: Any) -> str:
    """规格文本轻量归一：去空白、小写、统一常见单位写法。

    只用于“不同规格不能合并”的判定，不改变展示原值。
    """
    text = str(spec or "").strip().casefold()
    if not text:
        return ""
    # 常见中文单位归一到拉丁写法，避免 “750ml / 750毫升” 被误判成不同规格
    text = text.replace("毫升", "ml").replace("公升", "l").replace("升", "l")
    text = re.sub(r"\s+", "", text)
    return text


def goods_specs(code: str) -> list[str]:
    """收集档案已知的包装规格（档案字段 + 基线 + 匹配记录），返回展示原值。"""
    specs: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in specs:
            specs.append(text)

    goods = find_goods(code)
    if goods is not None:
        for key in ("包装规格", "规格", "归一规格", "package_spec", "spec"):
            add(goods.get(key))
    for baseline in store.rows("price_baseline"):
        if str(baseline.get("商品标识", "")) == code or str(
            baseline.get("原商品标识", ""),
        ) == code:
            add(baseline.get("归一规格"))
    for match in store.rows("sku_match"):
        if str(match.get("商品档案", "")) == code or str(
            match.get("原商品档案", ""),
        ) == code:
            add(match.get("候选规格"))
            add(match.get("归一规格"))
    return specs


def confirmed_target(code: str) -> str | None:
    """档案已确认并入的保留档案；没有则 None。"""
    goods = find_goods(code)
    if goods is not None:
        target = str(goods.get("并入档案", "") or "").strip()
        if target and target != code:
            return target
    for line in store.rows(LINEAGE_TABLE):
        if line.get("source_code") == code and line.get("status") == "已确认":
            return str(line.get("target_code", "")) or None
    return None


def resolve_attribution(code: Any) -> dict[str, Any]:
    """解析一个商品标识的当前归属与历史归属链。

    返回 current_code（当前保留档案）、chain（逐跳关系）、
    former_codes（所有能查到的旧所属档案）。
    """
    start = to_code(code) or str(code or "")
    chain: list[dict[str, str]] = []
    current = start
    seen = {current}
    while True:
        target = confirmed_target(current)
        if not target or target == current or target in seen:
            break
        chain.append({"from": current, "to": target})
        seen.add(target)
        current = target

    former: set[str] = set()
    # 归属链上除最终保留档案外都是旧档案
    for hop in chain:
        former.add(hop["from"])
    # 反向扫描：哪些档案（经一跳或多跳）归到 current
    for goods in goods_rows():
        candidate = str(goods.get("商品标识", ""))
        if not candidate or candidate == current:
            continue
        resolved = candidate
        walked: set[str] = {resolved}
        while True:
            nxt = confirmed_target(resolved)
            if not nxt or nxt == resolved or nxt in walked:
                break
            walked.add(nxt)
            resolved = nxt
        if resolved == current and candidate != current:
            former.add(candidate)
    former.discard(current)
    return {
        "queried_code": start,
        "current_code": current,
        "chain": chain,
        "former_codes": sorted(former),
    }


def find_order(order_ref: Any) -> dict[str, Any] | None:
    """按 id 或单号（HB-0001 / 客户端批次号）定位分组处理单。"""
    if order_ref is None:
        return None
    orders = store.rows(ORDER_TABLE)
    if isinstance(order_ref, int) or str(order_ref).strip().isdigit():
        oid = int(order_ref)
        for order in orders:
            if int(order.get("id", 0)) == oid:
                return order
    token = str(order_ref).strip()
    if not token:
        return None
    for order in orders:
        if str(order.get("单号", "")) == token:
            return order
        if str(order.get("batch_no", "")) == token:
            return order
        if str(order.get("submit_key", "")) == token:
            return order
        if str(order.get("批次号", "")) == token:
            return order
    return None


def order_status_of(groups: list[dict[str, Any]]) -> tuple[str, int]:
    """根据组合状态汇总处理单状态与可继续的合格组数。"""
    done = sum(1 for g in groups if g.get("status") == GROUP_DONE)
    waiting = sum(1 for g in groups if g.get("status") == GROUP_WAITING)
    pending = sum(1 for g in groups if g.get("status") == GROUP_PENDING)
    runnable = sum(1 for g in groups if g.get("status") == GROUP_PENDING
                   and not g.get("blocked"))
    total = len(groups)
    if total == 0:
        return ORDER_PENDING, 0
    if done == total:
        return ORDER_DONE, 0
    if runnable > 0:
        # 还有可直接继续的合格组
        return ORDER_PARTIAL if done else ORDER_PENDING, runnable
    if pending > 0:
        return ORDER_PARTIAL if done else ORDER_PENDING, pending
    if done and waiting:
        return ORDER_PARTIAL, 0
    if waiting and done == 0:
        return ORDER_WAITING, 0
    return ORDER_PENDING, 0
