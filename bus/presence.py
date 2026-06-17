"""Event-driven presence registry — NO heartbeat / polling.

Novel piece #2 vs cofiswarm (which uses HTTP /healthz liveness probes). Presence is
derived purely from asynchronous events:

  - `announce`                         -> component online
  - `goodbye`                          -> component offline (graceful)
  - no-responders / timeout when the middle man *needs* a component
                                       -> component offline + dependency-aware alert

There is no periodic probing anywhere. The "is it alive?" question is only ever asked
implicitly, at the moment a component is actually needed.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import ValidationError

from .subjects import Announce, Goodbye, ModelInfo, Presence as PresenceMsg, Status

logger = logging.getLogger(__name__)


class Record:
    __slots__ = ("component_id", "info", "infer_subject", "status")

    def __init__(self, component_id: str, info: ModelInfo, infer_subject: str, status: Status):
        self.component_id = component_id
        self.info = info
        self.infer_subject = infer_subject
        self.status = status


class Presence:
    def __init__(self) -> None:
        self._components: dict[str, Record] = {}
        self._models: dict[str, str] = {}  # model name -> component_id

    def apply_announce(self, data: dict) -> Optional[Announce]:
        try:
            ann = Announce(**data)
        except ValidationError:
            logger.error("Rejected malformed announce: %s", data, exc_info=True)
            return None
        self._components[ann.component_id] = Record(
            ann.component_id, ann.info, ann.infer_subject, Status.online
        )
        if ann.kind == "model":
            self._models[ann.info.name] = ann.component_id
        logger.info("Component ONLINE: %s (model=%s)", ann.component_id, ann.info.name)
        return ann

    def apply_goodbye(self, data: dict) -> Optional[str]:
        try:
            bye = Goodbye(**data)
        except ValidationError:
            logger.error("Rejected malformed goodbye: %s", data, exc_info=True)
            return None
        self._mark_offline(bye.component_id, bye.reason)
        return bye.component_id

    def mark_down(self, component_id: str, reason: str) -> None:
        """Called when a needed component fails to respond (ungraceful, no heartbeat)."""
        self._mark_offline(component_id, reason)

    def _mark_offline(self, component_id: str, reason: str) -> None:
        rec = self._components.get(component_id)
        if rec is None:
            return
        rec.status = Status.offline
        for name, cid in list(self._models.items()):
            if cid == component_id:
                del self._models[name]
        logger.info("Component OFFLINE: %s (%s)", component_id, reason)

    def model_component(self, name: str) -> Optional[str]:
        return self._models.get(name)

    def model_subject(self, name: str) -> Optional[str]:
        cid = self._models.get(name)
        rec = self._components.get(cid) if cid else None
        return rec.infer_subject if rec else None

    def models(self) -> list[str]:
        return sorted(self._models.keys())

    def snapshot(self) -> list[PresenceMsg]:
        """Current ONLINE components — used to seed a late-joining observer."""
        return [
            PresenceMsg(component_id=rec.component_id, status=Status.online, info=rec.info)
            for rec in self._components.values()
            if rec.status == Status.online
        ]
