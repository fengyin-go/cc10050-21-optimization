"""业务路由汇总：归并专用接口先注册，通用资源路由按模块 key 分派到领域服务。"""
from __future__ import annotations

from app.routers import merge_center, resource

ROUTERS = [merge_center, resource]
