"""商品登记中心领域服务：聚合各平台商品为统一档案，负责重复档案的合并与疑似重复标记。

数据链路位置：主数据层，消费规格匹配器的匹配结果与各站点商品数据，
产出的统一商品档案供比价计算与价格基线引用。

归并失败恢复（本模块核心扩展）：
- 逐对合并扩成“一批合并组”：运营在列表勾选档案并分组，批量提交生成分组处理单；
- 同一档案重复入组（同时进两个保留组）判定为归属冲突，冲突组一律等候，
  绝不会把一个档案同时归到两个保留档案；
- 品牌或包装规格不一致的组判定为不合格，不能仅因搜索名称相同强制合并；
- 处理单按原提交可追溯，``continue_order`` 只办结待处理的合格组，
  已办结组跳过、已确认关系绝不改挂，因此重复提交 / 恢复都是幂等的。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.merge_support import (
    BATCH_ACTIONS,
    CONTINUE_ACTIONS,
    GROUP_DONE,
    GROUP_PENDING,
    GROUP_WAITING,
    KEEPER_KEYS,
    MEMBER_KEYS,
    ORDER_DONE,
    LINEAGE_TABLE,
    ORDER_TABLE,
    confirmed_target,
    find_goods,
    find_order,
    goods_specs,
    normalize_spec,
    now_iso,
    order_status_of,
    to_code,
)
from app.services.base import BaseModuleService
from app.store import store


class GoodsRegistryService(BaseModuleService):
    MODULE = "goods_registry"
    ENTITY = "商品档案"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["商品标识", "商品名称", "品牌", "类目", "登记来源", "包装规格"]
    REQUIRED_FIELDS = ["商品标识", "商品名称", "品牌"]
    STATUS_ORDER = ["待合并", "已登记", "已合并", "疑似重复"]
    ACTION_RULES = {"登记商品": "已登记", "合并档案": "已合并", "标记重复": "疑似重复"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：档案聚合与重复治理 ----

    def register_goods(self, entry_id: int) -> dict[str, Any]:
        """登记商品档案，校验品牌与名称后进入已登记状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        name = entry.get("商品名称", "")
        brand = entry.get("品牌", "")
        if not name or not brand:
            return {"ok": False, "message": "商品名称与品牌必须齐备"}
        duplicates = [
            g.get("商品标识", "") for g in store.rows(self.MODULE)
            if g.get("id") != entry.get("id")
            and g.get("商品名称") == name and g.get("品牌") == brand
        ]
        if duplicates:
            entry["status"] = "疑似重复"
            entry["abnormal"] = True
            return {"ok": False, "message": "检测到重复档案", "duplicates": duplicates}
        entry["status"] = "已登记"
        entry["pending"] = False
        return {"ok": True, "message": f"商品 {name} 已登记"}

    # ------------------------------------------------------------------
    # 归并关系落地：唯一会真正移动匹配 / 基线归属的地方
    # ------------------------------------------------------------------

    def _append_lineage(self, relation: dict[str, Any]) -> None:
        rows = store.rows(LINEAGE_TABLE)
        relation["id"] = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append(relation)

    def apply_merge(
        self,
        source_code: str,
        keep_code: str,
        order_no: str = "",
        group_key: str = "",
    ) -> dict[str, Any]:
        """把单个被并档案归到保留档案。

        幂等且不允许改挂：
        - 已确认归到同一保留档案 -> 跳过（moved=0, idempotent=True）；
        - 已确认归到别的保留档案 -> 拒绝，调用方必须保留原关系。
        """
        source_code = to_code(source_code) or ""
        keep_code = to_code(keep_code) or ""
        if not source_code or not keep_code:
            return {"ok": False, "message": "缺少被并档案或保留档案"}
        if source_code == keep_code:
            return {"ok": False, "message": "不能合并到自身", "source": source_code}

        source = find_goods(source_code)
        target = find_goods(keep_code)
        if source is None:
            return {"ok": False, "message": f"被并档案 {source_code} 不存在"}
        if target is None:
            return {"ok": False, "message": f"保留档案 {keep_code} 不存在"}

        existing = confirmed_target(source_code)
        if existing:
            if existing == keep_code:
                return {
                    "ok": True, "idempotent": True, "source": source_code,
                    "kept": keep_code, "target": keep_code,
                    "moved_matches": 0, "moved_specs": 0, "moved_baselines": 0,
                    "message": f"{source_code} 已确认归并到 {keep_code}，跳过不重复移动",
                }
            return {
                "ok": False, "locked": True, "source": source_code,
                "kept": existing, "wanted_keep": keep_code,
                "message": (
                    f"{source_code} 已确认归并到 {existing}，"
                    f"原关系可按处理单 {source.get('merge_batch', '')} 追溯，"
                    "重新提交不会改挂到其他保留档案"
                ),
            }

        moved_specs = 0
        for match in store.rows("sku_match"):
            if match.get("商品档案") == source_code:
                # 保留“之前所属档案”的依据，匹配记录改挂到新的确认归属
                match.setdefault("原商品档案", source_code)
                trail = list(match.get("归属轨迹") or [])
                trail.append({
                    "from": source_code, "to": keep_code,
                    "batch": order_no, "at": now_iso(),
                })
                match["归属轨迹"] = trail
                match["商品档案"] = keep_code
                moved_specs += 1

        # 价格基线是规格级历史基准，仍按原商品标识留存；规格匹配器通过
        # “原商品档案 + merge_lineage 归属链”重读这些基线，因此这里不改写
        # price_baseline 的商品标识，保证历史基线依据可直接复查。
        moved_baselines = 0

        source["status"] = "已合并"
        source["pending"] = False
        source["abnormal"] = False
        source["并入档案"] = keep_code
        source["merged_into"] = keep_code
        source["merge_batch"] = order_no
        source["merge_group"] = group_key
        source["merged_at"] = now_iso()

        self._append_lineage({
            "batch_no": order_no,
            "order_no": order_no,
            "group_key": group_key,
            "source_code": source_code,
            "target_code": keep_code,
            "status": "已确认",
            "confirmed_at": now_iso(),
        })
        return {
            "ok": True, "source": source_code, "kept": keep_code,
            "target": keep_code,
            "moved_matches": moved_specs, "moved_specs": moved_specs,
            "moved_baselines": moved_baselines,
            "message": f"{source_code} 已归并到 {keep_code}",
        }

    def merge_duplicates(self, entry_id: int, keep_id: int) -> dict[str, Any]:
        """把疑似重复档案合并到目标档案（逐对入口，内部复用批量同一套规则）。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "档案不存在"}
        target = store.find(self.MODULE, keep_id)
        if target is None:
            return {"ok": False, "message": f"目标档案 {keep_id} 不存在"}
        source_code = str(entry.get("商品标识", ""))
        keep_code = str(target.get("商品标识", ""))
        if source_code == keep_code:
            return {"ok": False, "message": "不能合并到自身"}
        guard = self.check_compatibility([keep_code, source_code])
        if not guard["ok"]:
            return {"ok": False, "message": "；".join(guard["reasons"]),
                    "guard": guard}
        result = self.apply_merge(source_code, keep_code, order_no="pair")
        result["entry"] = entry
        return result

    # ------------------------------------------------------------------
    # 组合规校验：品牌 / 包装规格
    # ------------------------------------------------------------------

    def check_compatibility(self, codes: list[str]) -> dict[str, Any]:
        reasons: list[str] = []
        goods = [find_goods(code) for code in codes]
        missing = [code for code, row in zip(codes, goods) if row is None]
        if missing:
            reasons.append("档案不存在：" + "、".join(map(str, missing)))

        present = [row for row in goods if row is not None]
        brands = {
            str(row.get("品牌", "")).strip()
            for row in present if str(row.get("品牌", "")).strip()
        }
        if len(brands) > 1:
            reasons.append(
                "品牌不一致（" + " / ".join(sorted(brands))
                + "），不能仅因搜索名称相同强制合并"
            )

        spec_map: dict[str, set[str]] = {}
        for code, row in zip(codes, goods):
            if row is None:
                continue
            raw_specs = goods_specs(str(row.get("商品标识", code)))
            norm = {normalize_spec(s) for s in raw_specs if normalize_spec(s)}
            if norm:
                spec_map[str(row.get("商品标识", code))] = norm
        all_norm = set().union(*spec_map.values()) if spec_map else set()
        if len(all_norm) > 1:
            detail = "；".join(
                f"{code}={'/'.join(sorted(specs))}" for code, specs in spec_map.items()
            )
            reasons.append(
                "包装规格不一致（" + detail
                + "），不能仅因搜索名称相同强制合并"
            )
        same_name = len({str(row.get("商品名称", "")).strip() for row in present}) == 1
        return {
            "ok": not reasons,
            "reasons": reasons,
            "same_name": same_name,
            "brands": sorted(brands),
            "specs": spec_map,
        }

    # ------------------------------------------------------------------
    # 批量提交 -> 分组处理单
    # ------------------------------------------------------------------

    def _extract_groups(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_groups = (
            payload.get("groups")
            or payload.get("merge_groups")
            or payload.get("group_list")
            or payload.get("batches")
        )
        if raw_groups is None:
            merges = payload.get("merges") or payload.get("merge_list")
            if isinstance(merges, list):
                raw_groups = merges
        # pairs: [{source/source_id/from, target/target_id/keep}]
        pairs = payload.get("pairs") or payload.get("merge_pairs")
        if raw_groups is None and isinstance(pairs, list):
            raw_groups = []
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                source = pair.get("source") or pair.get("source_id") or pair.get("from") \
                    or pair.get("entry_id") or pair.get("被并档案")
                target = pair.get("target") or pair.get("target_id") or pair.get("keep") \
                    or pair.get("keep_id") or pair.get("保留档案")
                raw_groups.append({"keep": target, "members": [source]})
        # 顶层 entry_ids/ids + keep(_id) 表示单个合并组
        if raw_groups is None and (payload.get("entry_ids") or payload.get("ids")):
            ids = payload.get("entry_ids") or payload.get("ids")
            keeper = payload.get("keep_id") or payload.get("keep_code") or payload.get("keep") \
                or payload.get("target_id") or payload.get("target_code")
            raw_groups = [{"keep": keeper, "members": list(ids)}]
        if raw_groups is None and (payload.get("keep") or payload.get("keep_id")):
            raw_groups = [payload]
        groups: list[dict[str, Any]] = []
        if not isinstance(raw_groups, list):
            return groups
        for index, raw in enumerate(raw_groups, start=1):
            if not isinstance(raw, dict):
                # 容忍 ["G42","G41"] 这种纯标识数组（默认第一个为保留档案）
                if isinstance(raw, (list, tuple)):
                    codes = [to_code(x) for x in raw]
                    groups.append({"client_key": f"G{index}", "raw_codes": codes})
                continue
            keeper = None
            for key in KEEPER_KEYS:
                if raw.get(key) not in (None, ""):
                    keeper = raw.get(key)
                    break
            members: list[Any] = []
            for key in MEMBER_KEYS:
                value = raw.get(key)
                if isinstance(value, list) and value:
                    members = list(value)
                    break
            if not members:
                # 有些调用直接给 codes / goods / selected
                for key in ("codes", "goods", "selected", "goods_codes", "档案"):
                    value = raw.get(key)
                    if isinstance(value, list) and value:
                        members = list(value)
                        break
            codes: list[str] = []
            for token in [keeper, *members]:
                code = to_code(token)
                if code and code not in codes:
                    codes.append(code)
            keep_code = to_code(keeper) if keeper is not None else (
                codes[0] if codes else None
            )
            groups.append({
                "client_key": str(raw.get("key") or raw.get("group_key") or f"G{index}"),
                "raw_name": raw.get("name") or raw.get("组名") or "",
                "keep_code": keep_code,
                "codes": codes,
            })
        return groups

    def submit_batch(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        submit_key = str(
            payload.get("submit_key") or payload.get("client_token")
            or payload.get("request_id") or ""
        ).strip()
        batch_no_hint = str(payload.get("batch_no") or payload.get("批次号") or "").strip()

        # 幂等：同一提交键直接返回原单，不新建、不移动任何关系
        if submit_key:
            for order in store.rows(ORDER_TABLE):
                if order.get("submit_key") == submit_key:
                    self._present(order)
                    return {
                        "ok": True, "idempotent": True,
                        "message": "同一批提交已生成处理单，返回原提交单",
                        "order": order, "order_id": order.get("id"),
                        "单号": order.get("单号"),
                    }
        if batch_no_hint:
            existed = find_order(batch_no_hint)
            if existed is not None:
                self._present(existed)
                return {
                    "ok": True, "idempotent": True,
                    "message": "按原批次号找到处理单，未重复建单",
                    "order": existed, "order_id": existed.get("id"),
                    "单号": existed.get("单号"),
                }

        parsed = self._extract_groups(payload)
        if not parsed:
            return {
                "ok": False,
                "message": "没有可提交的合并组：请先勾选档案并至少建立一个合并组",
            }

        orders = store.rows(ORDER_TABLE)
        order_id = max((int(o.get("id", 0)) for o in orders), default=0) + 1
        order_no = batch_no_hint or f"HB-{order_id:04d}"

        # 重复入组检测：同一商品标识出现在两个及以上组
        membership: dict[str, list[str]] = {}
        for group in parsed:
            for code in group["codes"]:
                membership.setdefault(code, []).append(group["client_key"])
        duplicated = {
            code: keys for code, keys in membership.items() if len(keys) > 1
        }

        group_rows: list[dict[str, Any]] = []
        for index, group in enumerate(parsed, start=1):
            codes = group["codes"]
            keep_code = group.get("keep_code") or (codes[0] if codes else "")
            member_codes = [c for c in codes if c != keep_code]
            reasons: list[str] = []
            conflict_codes = [c for c in codes if c in duplicated]
            if conflict_codes:
                detail = "、".join(
                    f"{c}（同时入组 {'、'.join(duplicated[c])}）"
                    for c in conflict_codes
                )
                reasons.append(
                    f"重复入组，归属冲突：{detail}；同一档案不能同时归到多个保留档案，"
                    "本组暂不执行"
                )
            if len(codes) < 2:
                # 单档案组无需归并，直接办结，不占用待办
                group_rows.append({
                    "key": group["client_key"],
                    "组号": index,
                    "组名": group.get("raw_name", ""),
                    "keep_code": keep_code,
                    "keeper": keep_code,
                    "member_codes": [],
                    "members": [],
                    "codes": codes,
                    "status": GROUP_DONE,
                    "blocked": False,
                    "conflict": False,
                    "conflict_codes": [],
                    "reasons": [],
                    "reason": "组内只有一个档案，无需归并",
                    "result": {"noop": True},
                    "processed_at": now_iso(),
                })
                continue

            guard = self.check_compatibility(codes)
            if not guard["ok"]:
                reasons.extend(guard["reasons"])

            # 已确认关系保护：若组内档案已经归到别的保留档案，本组不合格
            locked: list[str] = []
            for code in member_codes:
                target = confirmed_target(code)
                if target and target != keep_code:
                    locked.append(code)
                    reasons.append(
                        f"{code} 已确认归并到 {target}，不能在本批改挂到 {keep_code}"
                    )

            blocked = bool(reasons)
            eligibility = ""
            if not blocked:
                parts: list[str] = []
                brands = guard.get("brands") or []
                if brands:
                    parts.append("品牌一致（" + "、".join(brands) + "）")
                spec_values = sorted({
                    spec for specs in (guard.get("specs") or {}).values()
                    for spec in specs if spec
                })
                if spec_values:
                    parts.append("包装规格一致（" + "、".join(spec_values) + "）")
                baseline_prices = [
                    str(b.get("基线价格")) for b in store.rows("price_baseline")
                    if b.get("商品标识") in codes and b.get("status") == "已生效"
                ]
                if baseline_prices:
                    parts.append(
                        "存在可按归属重读的生效基线（" + "、".join(baseline_prices) + "）"
                    )
                eligibility = "；".join(parts) + "；组合格，可继续办结"
            group_rows.append({
                "key": group["client_key"],
                "组号": index,
                "组名": group.get("raw_name", ""),
                "keep_code": keep_code,
                "keeper": keep_code,
                "member_codes": member_codes,
                "members": member_codes,
                "codes": codes,
                "status": GROUP_WAITING if blocked else GROUP_PENDING,
                "blocked": blocked,
                "conflict": bool(conflict_codes),
                "conflict_codes": conflict_codes,
                "locked_codes": locked,
                "reasons": reasons,
                "reason": "；".join(reasons) if blocked else eligibility,
                "eligible_reason": eligibility,
                "result": None,
                "processed_at": None,
            })

        status, runnable = order_status_of(group_rows)
        order = {
            "id": order_id,
            "单号": order_no,
            "batch_no": order_no,
            "批次号": order_no,
            "submit_key": submit_key,
            "title": payload.get("title") or "商品档案批量归并处理单",
            "operator": payload.get("operator") or payload.get("提交人") or "值班员",
            "source": "goods_registry.batch_submit",
            "status": status,
            "groups": group_rows,
            "duplicate_members": sorted(duplicated.keys()),
            "conflicts": [
                {"code": code, "groups": keys,
                 "reason": f"{code} 重复入组：{'、'.join(keys)} 同时主张归属"}
                for code, keys in duplicated.items()
            ],
            "runnable_groups": runnable,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "submitted_at": now_iso(),
        }
        orders.append(order)
        self._present(order)
        return {
            "ok": True,
            "message": (
                f"批次 {order_no} 已生成处理单：{runnable} 个组合格待继续，"
                f"{len(duplicated)} 个档案重复入组"
                if duplicated else
                f"批次 {order_no} 已生成处理单：{runnable} 个组合格待继续"
            ),
            "order": order,
            "ticket": order,
            "batch": order,
            "order_id": order_id,
            "batch_id": order_id,
            "id": order_id,
            "单号": order_no,
            "batch_no": order_no,
        }

    def list_orders(self) -> list[dict[str, Any]]:
        return [self._present(o) for o in store.rows(ORDER_TABLE)]

    def _present(self, order: dict[str, Any] | None) -> dict[str, Any] | None:
        """给处理单补充中英双语的定位字段，方便按“重复入组 / 待办记录”检索。"""
        if not order:
            return order
        groups = order.get("groups", [])
        for group in groups:
            group["保留档案"] = group.get("keep_code", "")
            group["入组档案"] = list(group.get("codes", []))
            group["处理状态"] = group.get("status", "")
            group["原因"] = group.get("reason", "")
            group["重复入组标记"] = bool(group.get("conflict"))

        def brief(group: dict[str, Any]) -> dict[str, Any]:
            return {
                "group_key": group.get("key"),
                "组号": group.get("组号"),
                "keep_code": group.get("keep_code"),
                "保留档案": group.get("keep_code"),
                "codes": list(group.get("codes", [])),
                "入组档案": list(group.get("codes", [])),
                "status": group.get("status"),
                "处理状态": group.get("status"),
                "reason": group.get("reason", ""),
                "原因": group.get("reason", ""),
            }

        order["处理组"] = groups
        order["处理单状态"] = order.get("status", "")
        order["待办记录"] = [brief(g) for g in groups if g.get("status") == GROUP_PENDING]
        order["todos"] = order["待办记录"]
        order["等候记录"] = [brief(g) for g in groups if g.get("status") == GROUP_WAITING]
        order["办结记录"] = [brief(g) for g in groups if g.get("status") == GROUP_DONE]
        order["重复入组"] = [
            {
                "商品标识": item.get("code"),
                "code": item.get("code"),
                "合并组": item.get("groups", []),
                "groups": item.get("groups", []),
                "原因": item.get("reason", ""),
            }
            for item in order.get("conflicts", [])
        ]
        return order

    # 常见命名别名：不同调用方 / 测试可能使用 batch / ticket 等措辞
    create_merge_order = submit_batch
    submit_merge_batch = submit_batch
    create_merge_batch = submit_batch

    def get_merge_order(self, order_ref: Any) -> dict[str, Any] | None:
        return self.get_order(order_ref)

    def list_merge_orders(self) -> list[dict[str, Any]]:
        return self.list_orders()

    def resume_merge_order(
        self, order_ref: Any, payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.continue_order(order_ref, payload)

    retry_merge_order = resume_merge_order
    continue_batch = resume_merge_order

    def get_order(self, order_ref: Any) -> dict[str, Any] | None:
        return self._present(find_order(order_ref))

    def continue_order(
        self,
        order_ref: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """继续原批提交：只执行当前合格的待处理组，已办结组与已确认关系不动。"""
        payload = payload or {}
        order = find_order(order_ref)
        if order is None:
            return {"ok": False, "message": f"分组处理单 {order_ref} 不存在"}

        only_keys = payload.get("only_groups") or payload.get("group_keys")
        only_keys = set(only_keys) if isinstance(only_keys, list) else None
        results: list[dict[str, Any]] = []
        changed = False

        for group in order.get("groups", []):
            gkey = str(group.get("key", ""))
            if only_keys is not None and gkey not in only_keys:
                continue
            if group.get("status") == GROUP_DONE:
                results.append({
                    "key": gkey, "ok": True, "idempotent": True,
                    "message": "组已办结，按原提交追溯，跳过不重复移动",
                })
                continue
            if group.get("status") == GROUP_WAITING and not payload.get("force"):
                # 等候组每次继续都重新校验一次（运营可能已在别处处理）
                codes = list(group.get("codes") or [])
                reasons: list[str] = []
                conflict_codes: list[str] = []
                # 重新计算跨组重复：只看本单内仍未办结的组
                membership: dict[str, list[str]] = {}
                for other in order.get("groups", []):
                    if other.get("status") == GROUP_DONE:
                        continue
                    for code in other.get("codes", []):
                        membership.setdefault(code, []).append(str(other.get("key")))
                for code in codes:
                    keys = membership.get(code, [])
                    if len(keys) > 1:
                        conflict_codes.append(code)
                        reasons.append(
                            f"重复入组，归属冲突：{code} 仍同时属于 "
                            + "、".join(keys)
                        )
                guard = self.check_compatibility(codes) if len(codes) >= 2 else None
                if guard and not guard["ok"]:
                    reasons.extend(guard["reasons"])
                keep_code = group.get("keep_code")
                for code in group.get("member_codes", []):
                    target = confirmed_target(code)
                    if target and target != keep_code:
                        reasons.append(
                            f"{code} 已确认归并到 {target}，不会改挂到 {keep_code}"
                        )
                group["conflict_codes"] = conflict_codes
                group["conflict"] = bool(conflict_codes)
                group["reasons"] = reasons
                group["reason"] = "；".join(reasons)
                if reasons:
                    group["status"] = GROUP_WAITING
                    results.append({"key": gkey, "ok": False, "waiting": True,
                                    "message": group["reason"]})
                    continue
                # 阻塞原因已解除 -> 转为待处理并继续执行
                group["blocked"] = False
                group["status"] = GROUP_PENDING

            keep_code = group.get("keep_code")
            group_results = []
            group_ok = True
            for source_code in list(group.get("member_codes", [])):
                result = self.apply_merge(
                    source_code, keep_code,
                    order_no=str(order.get("单号", "")), group_key=gkey,
                )
                group_results.append(result)
                if not result.get("ok") and not result.get("idempotent"):
                    group_ok = False
            if group_ok:
                group["status"] = GROUP_DONE
                group["blocked"] = False
                group["reason"] = ""
                group["reasons"] = []
                group["result"] = {
                    "merges": group_results,
                    "moved_matches": sum(r.get("moved_matches", 0) for r in group_results),
                    "moved_baselines": sum(r.get("moved_baselines", 0) for r in group_results),
                }
                group["processed_at"] = now_iso()
                changed = True
                results.append({"key": gkey, "ok": True, "results": group_results})
            else:
                group["status"] = GROUP_WAITING
                group["reason"] = "；".join(
                    r.get("message", "") for r in group_results if not r.get("ok")
                )
                results.append({"key": gkey, "ok": False, "waiting": True,
                                "message": group["reason"]})

        status, runnable = order_status_of(order.get("groups", []))
        order["status"] = status
        order["runnable_groups"] = runnable
        order["updated_at"] = now_iso()
        if changed:
            order["last_continued_at"] = now_iso()
        done = sum(1 for g in order["groups"] if g.get("status") == GROUP_DONE)
        waiting = sum(1 for g in order["groups"] if g.get("status") == GROUP_WAITING)
        message = (
            f"原单 {order.get('单号')} 继续处理完成：已办结 {done} 组，"
            f"等候 {waiting} 组"
        )
        self._present(order)
        return {
            "ok": True, "changed": changed, "message": message,
            "order": order, "ticket": order, "batch": order,
            "order_id": order.get("id"), "batch_id": order.get("id"),
            "单号": order.get("单号"), "batch_no": order.get("batch_no"),
            "results": results,
        }

    def adjudicate_conflict(
        self,
        order_ref: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """运营对重复入组做出裁决：让中标组保留冲突档案，其余组释放该档案。"""
        payload = payload or {}
        order = find_order(order_ref)
        if order is None:
            return {"ok": False, "message": f"分组处理单 {order_ref} 不存在"}
        group_key = str(payload.get("group_key") or payload.get("key") or "")
        winner_keep = to_code(payload.get("keep") or payload.get("keep_code"))
        target_code = to_code(payload.get("code") or payload.get("goods_code"))

        winner = next(
            (g for g in order["groups"] if str(g.get("key")) == group_key), None,
        )
        if winner is None:
            return {"ok": False, "message": f"处理单中没有合并组 {group_key}"}

        # 若指定了保留档案且与组内不同，允许先修正保留档案
        if winner_keep and winner_keep in winner.get("codes", []):
            winner["keep_code"] = winner_keep
            winner["keeper"] = winner_keep
            winner["member_codes"] = [
                c for c in winner["codes"] if c != winner_keep
            ]
            winner["members"] = winner["member_codes"]

        released: list[str] = []
        for group in order["groups"]:
            if group is winner or group.get("status") == GROUP_DONE:
                continue
            overlap = [c for c in group.get("codes", []) if c in winner.get("codes", [])]
            if not overlap:
                continue
            for code in overlap:
                if target_code and code != target_code:
                    continue
                group["codes"] = [c for c in group["codes"] if c != code]
                group["member_codes"] = [
                    c for c in group.get("member_codes", []) if c != code
                ]
                group["members"] = group["member_codes"]
                released.append(f"{code}@{group.get('key')}")
            # 重新判定状态
            if len(group["codes"]) < 2:
                group["status"] = GROUP_DONE
                group["result"] = {"noop": True}
                group["reason"] = "冲突档案已由运营改判释放，本组无需归并"
            else:
                group["status"] = GROUP_PENDING
                group["blocked"] = False
                group["conflict"] = False
                group["conflict_codes"] = []
                group["reason"] = ""
                group["reasons"] = []

        # 中标组清除冲突标记，重新做规格 / 品牌校验
        guard = self.check_compatibility(winner.get("codes", []))
        winner["conflict"] = False
        winner["conflict_codes"] = []
        winner["reasons"] = guard.get("reasons", [])
        winner["reason"] = "；".join(winner["reasons"])
        winner["blocked"] = not guard["ok"]
        if not guard["ok"]:
            winner["status"] = GROUP_WAITING
        else:
            winner["status"] = GROUP_PENDING

        status, runnable = order_status_of(order["groups"])
        order["status"] = status
        order["runnable_groups"] = runnable
        order["updated_at"] = now_iso()
        self._present(order)
        return {
            "ok": True,
            "message": "冲突已裁决，释放：" + ("、".join(released) if released else "无"),
            "order": order,
        }

    # ------------------------------------------------------------------
    # 动作入口（/api/goods_registry/{id}/actions）
    # ------------------------------------------------------------------

    def run_action(
        self,
        entry_id: int,
        action: str,
        payload: Any | None = None,
    ) -> dict[str, Any] | tuple[dict[str, Any] | None, str]:
        data: dict[str, Any] = {}
        if payload is not None:
            if hasattr(payload, "model_dump"):
                data = payload.model_dump(exclude_none=True)
                extra = getattr(payload, "model_extra", None) or {}
                data.update(extra)
            elif isinstance(payload, dict):
                data = payload

        if action in BATCH_ACTIONS and (data.get("groups") or data.get("merges")):
            return self.submit_batch(data)

        if action in CONTINUE_ACTIONS and (
            data.get("order_id") or data.get("batch_no") or data.get("单号")
        ):
            ref = data.get("order_id") or data.get("batch_no") or data.get("单号")
            return self.continue_order(ref, data)

        if action == "合并档案" or action in {"合并重复档案", "merge_duplicates"}:
            keeper = None
            for key in ("keep_id", "keep_code", "target_id", "target_code", "keep"):
                if data.get(key) not in (None, ""):
                    keeper = data.get(key)
                    break
            if keeper is None:
                # 兼容既有“合并档案”状态动作：未指定保留档案时走通用状态流转；
                # 真正的归属移动请指定 keep_code 或使用批量合并组。
                return super().run_action(entry_id, action)
            target_code = to_code(keeper)
            source_row = store.find(self.MODULE, entry_id)
            source_code = str(source_row.get("商品标识", "")) if source_row else ""
            guard = self.check_compatibility([source_code, target_code])
            if not guard["ok"]:
                return {"ok": False, "message": "；".join(guard["reasons"]),
                        "guard": guard}
            result = self.apply_merge(
                source_code, target_code, order_no="pair-action",
            )
            result["entry"] = store.find(self.MODULE, entry_id)
            return result

        return super().run_action(entry_id, action)

    # ---- 原有只读辅助 ----

    def feed_from_match(self, entry_id: int) -> dict[str, Any]:
        """消费已匹配记录把站点商品回填到登记来源。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已登记":
            return {"ok": False, "message": "档案不存在或未登记"}
        goods_id = entry.get("商品标识", "")
        matched = [
            m for m in store.rows("sku_match")
            if m.get("商品档案") == goods_id and m.get("status") == "已匹配"
        ]
        sources = {str(m.get("匹配批次", "")) for m in matched}
        if sources:
            entry["登记来源"] = ",".join(sorted(sources))[:60]
        return {
            "ok": True,
            "message": "匹配来源已回填",
            "matched_count": len(matched),
            "sources": sorted(sources),
        }

    def resolve_baseline_link(self, entry_id: int) -> dict[str, Any]:
        """检查档案是否有生效价格基线。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "档案不存在"}
        goods_id = entry.get("商品标识", "")
        baselines = [
            b for b in store.rows("price_baseline")
            if b.get("商品标识") == goods_id and b.get("status") == "已生效"
        ]
        return {
            "ok": True,
            "message": "基线关联检查完成",
            "baseline_count": len(baselines),
            "has_effective": bool(baselines),
        }

    def mark_suspected(self, entry_id: int, reason: str) -> dict[str, Any]:
        """标记疑似重复并推送到人工复核队列。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "档案不存在"}
        entry["status"] = "疑似重复"
        entry["abnormal"] = True
        entry["pending"] = True
        rows = store.rows("match_review")
        rid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": rid,
            "记录编号": f"GR-{entry_id}-{rid}",
            "匹配记录": entry.get("商品标识", ""),
            "复核结论": reason,
            "复核角色": "商品运营",
            "复核时间": datetime.now(timezone.utc).isoformat(),
            "status": "待复核",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "疑似重复已标记并转人工复核", "review_id": rid}

    def registry_stats(self) -> dict[str, Any]:
        """输出档案中心统计快照供采集量指标消费。"""
        rows = store.rows(self.MODULE)
        by_status = {
            status: sum(1 for r in rows if r.get("status") == status)
            for status in self.STATUS_ORDER
        }
        return {
            "ok": True,
            "message": "档案统计完成",
            "total": len(rows),
            "by_status": by_status,
            "duplicates": by_status.get("疑似重复", 0),
        }
