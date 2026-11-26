"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.shop_site import ShopSiteService
from app.services.price_crawler import PriceCrawlerService
from app.services.page_parser import PageParserService
from app.services.spec_normalizer import SpecNormalizerService
from app.services.currency_conv import CurrencyConvService
from app.services.price_baseline import PriceBaselineService
from app.services.price_compare import PriceCompareService
from app.services.price_alert import PriceAlertService
from app.services.goods_registry import GoodsRegistryService
from app.services.sku_match import SkuMatchService
from app.services.sample_store import SampleStoreService
from app.services.parse_audit import ParseAuditService
from app.services.crawl_schedule import CrawlScheduleService
from app.services.rate_limit import RateLimitService
from app.services.proxy_route import ProxyRouteService
from app.services.failure_quarantine import FailureQuarantineService
from app.services.price_export import PriceExportService
from app.services.change_history import ChangeHistoryService
from app.services.match_review import MatchReviewService
from app.services.collect_metrics import CollectMetricsService
from app.services.merge_order import MergeOrderService

SERVICES: dict[str, BaseModuleService] = {
    "shop_site": ShopSiteService(),
    "price_crawler": PriceCrawlerService(),
    "page_parser": PageParserService(),
    "spec_normalizer": SpecNormalizerService(),
    "currency_conv": CurrencyConvService(),
    "price_baseline": PriceBaselineService(),
    "price_compare": PriceCompareService(),
    "price_alert": PriceAlertService(),
    "goods_registry": GoodsRegistryService(),
    "sku_match": SkuMatchService(),
    "sample_store": SampleStoreService(),
    "parse_audit": ParseAuditService(),
    "crawl_schedule": CrawlScheduleService(),
    "rate_limit": RateLimitService(),
    "proxy_route": ProxyRouteService(),
    "failure_quarantine": FailureQuarantineService(),
    "price_export": PriceExportService(),
    "change_history": ChangeHistoryService(),
    "match_review": MatchReviewService(),
    "collect_metrics": CollectMetricsService(),
}

# 分组处理单是归并批次的资源视图，用同一个服务实例挂多个直观资源名，
# 方便按“处理单 / 批次单”等不同叫法查询与对接。
_merge_order_service = MergeOrderService()
for _alias in (
    "merge_order", "merge_orders", "merge-order", "merge-orders",
    "goods_merge_order", "goods_merge_orders",
    "goods-merge-order", "goods-merge-orders",
    "merge_ticket", "merge_tickets", "merge-ticket", "merge-tickets",
    "merge_batch", "merge_batches", "merge-batch", "merge-batches",
    "goods_merge_batch", "goods-merge-batch",
):
    SERVICES[_alias] = _merge_order_service


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
