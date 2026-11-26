"""规格匹配器领域服务：把站点SKU匹配到商品档案的归一规格，负责匹配置信度判定与人工复核分流。

数据链路位置：规格对齐层，消费规格归一器推送的候选规格与商品档案中心的主数据，
低置信度结果转人工复核队列，高置信度结果回填商品档案。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.merge_support import (
    confirmed_target,
    find_goods,
    goods_specs,
    normalize_spec,
    resolve_attribution,
    to_code,
)
from app.services.base import BaseModuleService
from app.store import store


class SkuMatchService(BaseModuleService):
    MODULE = "sku_match"
    ENTITY = "匹配记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["匹配批次", "商品档案", "候选规格", "匹配置信度", "匹配状态"]
    REQUIRED_FIELDS = ["匹配批次", "商品档案", "候选规格"]
    STATUS_ORDER = ["待匹配", "已匹配", "人工复核", "匹配失败"]
    ACTION_RULES = {"执行匹配": "已匹配", "转人工复核": "人工复核"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：规格匹配与置信度分流 ----

    def run_match(self, entry_id: int, confidence: float) -> dict[str, Any]:
        """执行规格匹配，按置信度分流到已匹配或人工复核。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        goods_name = entry.get("商品档案", "")
        goods = next(
            (g for g in store.rows("goods_registry") if g.get("商品标识") == goods_name), None
        )
        if goods is None:
            entry["status"] = "匹配失败"
            entry["abnormal"] = True
            return {"ok": False, "message": f"商品档案 {goods_name} 不存在"}
        if confidence >= 0.95:
            entry["status"] = "已匹配"
            entry["pending"] = False
        else:
            entry["status"] = "人工复核"
            entry["pending"] = True
        entry["匹配置信度"] = f"{confidence:.2f}"
        return {
            "ok": True,
            "message": "匹配执行完成",
            "result_status": entry.get("status"),
            "confidence": entry.get("匹配置信度"),
        }

    def escalate_review(self, entry_id: int) -> dict[str, Any]:
        """把低置信度匹配记录升级到人工复核队列。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "人工复核":
            return {"ok": False, "message": "记录不存在或不处于人工复核状态"}
        rows = store.rows("match_review")
        rid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": rid,
            "记录编号": f"SM-{entry_id}-{rid}",
            "匹配记录": str(entry.get("id", "")),
            "复核结论": f"置信度 {entry.get('匹配置信度', '')}",
            "复核角色": "规格审核员",
            "复核时间": datetime.now(timezone.utc).isoformat(),
            "status": "待复核",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "已升级人工复核", "review_id": rid}

    def apply_review_result(self, entry_id: int, approved: bool) -> dict[str, Any]:
        """应用复核结论：通过则回填档案，驳回则进失败隔离。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "记录不存在"}
        if approved:
            entry["status"] = "已匹配"
            entry["pending"] = False
            return {"ok": True, "message": "复核通过，匹配已确认"}
        entry["status"] = "匹配失败"
        entry["abnormal"] = True
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"match-{entry_id}",
            "失败阶段": "匹配",
            "失败原因": "人工复核驳回",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "0",
            "status": "待处理",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "复核驳回，样本已隔离", "quarantine_id": fid}

    def consume_normalized(self, entry_id: int) -> dict[str, Any]:
        """消费规格归一器推送的候选规格完成对齐。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "记录不存在"}
        spec = entry.get("候选规格", "")
        matched_rule = next(
            (r for r in store.rows("spec_normalizer")
             if r.get("status") == "已启用" and r.get("归一规格") == spec), None
        )
        if matched_rule is None:
            entry["status"] = "匹配失败"
            entry["abnormal"] = True
            return {"ok": False, "message": f"候选规格 {spec} 没有启用中的归一规则"}
        entry["匹配状态"] = "归一对齐"
        return {
            "ok": True,
            "message": "候选规格已对齐归一规则",
            "rule": matched_rule.get("规则名称", ""),
        }

    def link_goods_baseline(self, entry_id: int) -> dict[str, Any]:
        """检查匹配成功后商品是否具备基线价格。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已匹配":
            return {"ok": False, "message": "记录不存在或未匹配"}
        goods_name = entry.get("商品档案", "")
        has_baseline = any(
            b.get("商品标识") == goods_name and b.get("status") == "已生效"
            for b in store.rows("price_baseline")
        )
        return {
            "ok": True,
            "message": "基线关联检查完成",
            "goods": goods_name,
            "has_baseline": has_baseline,
        }

    # ------------------------------------------------------------------
    # 归并后的确认归属 + 基线重读 + 旧档案追溯
    # ------------------------------------------------------------------

    def spec_check(self, code_or_id: Any, spec: str | None = None) -> dict[str, Any]:
        """按商品档案核对规格与基线。

        - 先从归并数据解析当前确认归属（被并档案 -> 保留档案）；
        - 用当前归属重读每个匹配使用的价格基线，同时沿旧标识 / 归属链兜底；
        - 保留原商品档案、原商品标识等“之前所属档案”的查询依据。
        """
        queried = to_code(code_or_id) or str(code_or_id or "")
        attribution = resolve_attribution(queried)
        current = attribution["current_code"]
        all_codes = {queried, current, *attribution["former_codes"]}

        goods = find_goods(current) or find_goods(queried)
        specs = goods_specs(current)
        for old in attribution["former_codes"]:
            for s in goods_specs(old):
                if s not in specs:
                    specs.append(s)

        matches = [
            m for m in store.rows(self.MODULE)
            if m.get("商品档案") in all_codes
            or m.get("原商品档案") in all_codes
        ]
        baselines = [
            b for b in store.rows("price_baseline")
            if b.get("商品标识") in all_codes
            or b.get("原商品标识") in all_codes
        ]

        wanted_norm = normalize_spec(spec) if spec else ""

        def baseline_spec_norm(b: dict[str, Any]) -> str:
            return normalize_spec(b.get("归一规格"))

        effective = [b for b in baselines if b.get("status") == "已生效"]
        chosen: dict[str, Any] | None = None
        if wanted_norm:
            chosen = next(
                (b for b in effective if baseline_spec_norm(b) == wanted_norm), None,
            )
        if chosen is None:
            # 优先取与某条匹配候选规格一致的生效基线
            match_norms = {
                normalize_spec(m.get("候选规格")) for m in matches
                if normalize_spec(m.get("候选规格"))
            }
            chosen = next(
                (b for b in effective if baseline_spec_norm(b) in match_norms), None,
            )
        if chosen is None and effective:
            chosen = effective[0]

        baseline_views = []
        for b in baselines:
            baseline_views.append({
                "id": b.get("id"),
                "goods_code": b.get("商品标识"),
                "商品标识": b.get("商品标识"),
                "spec": b.get("归一规格"),
                "归一规格": b.get("归一规格"),
                "price": b.get("基线价格"),
                "基线价格": b.get("基线价格"),
                "status": b.get("status"),
                "former_goods_code": b.get("原商品标识", ""),
                "原商品标识": b.get("原商品标识", ""),
                "采样窗口": b.get("采样窗口", ""),
            })

        match_views = []
        for m in matches:
            # 每个匹配按“当前归属 + 原商品档案”重读它使用的规格基线
            match_codes = {
                str(m.get("商品档案", "")), str(m.get("原商品档案", "")),
                current, *attribution["former_codes"],
            }
            candidate_norm = normalize_spec(m.get("候选规格"))
            own_baselines = [
                b for b in effective
                if b.get("商品标识") in match_codes
                or b.get("原商品标识") in match_codes
            ]
            own = next(
                (b for b in own_baselines
                 if normalize_spec(b.get("归一规格")) == candidate_norm),
                own_baselines[0] if own_baselines else None,
            )
            match_views.append({
                "id": m.get("id"),
                "batch": m.get("匹配批次"),
                "匹配批次": m.get("匹配批次"),
                "goods_code": m.get("商品档案"),
                "商品档案": m.get("商品档案"),
                "former_goods_code": m.get("原商品档案", ""),
                "原商品档案": m.get("原商品档案", ""),
                "candidate_spec": m.get("候选规格"),
                "候选规格": m.get("候选规格"),
                "confidence": m.get("匹配置信度"),
                "status": m.get("status"),
                "匹配状态": m.get("匹配状态"),
                "baseline_price": own.get("基线价格") if own else None,
                "baseline": own,
            })

        return {
            "ok": goods is not None or bool(matches or baselines),
            "message": (
                f"当前归属 {current}"
                + (f"（由 {queried} 归并）" if queried != current else "")
                + f"：规格 {('、'.join(specs) or '未登记')}，"
                + (f"基线价格 {chosen.get('基线价格')}" if chosen else "暂无生效基线")
            ),
            "queried_code": queried,
            "goods_code": current,
            "current_code": current,
            "商品档案": current,
            "former_codes": attribution["former_codes"],
            "chain": attribution["chain"],
            "goods": goods,
            "specs": specs,
            "baselines": baseline_views,
            "matches": match_views,
            "baseline": chosen,
            "baseline_price": chosen.get("基线价格") if chosen else None,
            "has_baseline": chosen is not None,
        }

    def check_entry_baseline(self, entry_id: int) -> dict[str, Any]:
        """从一条匹配记录出发，按其（可能已更新的）商品档案重读基线。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        code = entry.get("商品档案", "")
        result = self.spec_check(code, entry.get("候选规格"))
        result["entry"] = entry
        result["entry_id"] = entry_id
        return result

    def list_entries(self, **kwargs: Any):
        items, total = super().list_entries(**kwargs)
        enriched: list[dict[str, Any]] = []
        for row in items:
            view = dict(row)
            try:
                check = self.spec_check(row.get("商品档案", ""), row.get("候选规格"))
                view["baseline_price"] = check.get("baseline_price")
                view["has_baseline"] = check.get("has_baseline")
                view["current_goods_code"] = check.get("current_code")
                view["former_goods_codes"] = check.get("former_codes")
            except Exception:
                # 列表增强字段不能影响主列表可用性
                pass
            enriched.append(view)
        return enriched, total

    def run_action(
        self,
        entry_id: int,
        action: str,
        payload: Any | None = None,
    ) -> dict[str, Any] | tuple[dict[str, Any] | None, str]:
        if action in {
            "核对基线", "基线核对", "重读基线", "检查基线", "规格基线核对",
            "核对规格基线", "check_baseline", "spec_check",
        }:
            return self.check_entry_baseline(entry_id)
        return super().run_action(entry_id, action, payload)

    def batch_summary(self, batch: str) -> dict[str, Any]:
        """汇总匹配批次内各状态数量并上报指标。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("匹配批次") == batch]
        if not rows:
            return {"ok": False, "message": f"批次 {batch} 不存在"}
        matched = sum(1 for r in rows if r.get("status") == "已匹配")
        review = sum(1 for r in rows if r.get("status") == "人工复核")
        return {
            "ok": True,
            "message": "批次汇总完成",
            "total": len(rows),
            "matched": matched,
            "need_review": review,
            "failed": len(rows) - matched - review,
        }
