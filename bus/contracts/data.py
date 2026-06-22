"""Data-service contracts: one uniform Repository envelope over the bus (S4).

Replaces the legacy `/api/memory,cache*,history*,logs,rag/health,swarm-config,models`. Every
data resource answers the same `DataQuery` -> `DataReply` shape (Repository pattern), so a new
resource is just another handler — no new envelope per resource.
"""
from __future__ import annotations

from typing import Any, Optional

from .base import Envelope, ServiceReply


class DataQuery(Envelope):
    resource: str                       # memory|cache|history|rag|logs|swarm-config|models
    op: str = "get"                     # get | list | put | delete | health
    key: str = ""
    value: Optional[Any] = None         # for put
    params: dict[str, Any] = {}         # resource-specific, e.g. {"limit": 50}


class DataReply(ServiceReply):
    resource: str = ""
    op: str = ""
    items: list[Any] = []               # for list/history/logs
    value: Optional[Any] = None         # for get
    count: int = 0
