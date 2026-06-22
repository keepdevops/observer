"""Observer GUI: an aiohttp web app that bridges the NATS bus to the browser.

This is the "Observer" in the design — it only *observes* bus state and *dispatches*
requests. It subscribes to presence + alert streams and forwards them to every connected
browser over WebSocket, and it streams Token chunks back live for prompts the user sends.
It owns no business logic; the middle man remains the single coordinator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType, web

from bus import subjects as S
from bus.nats_bus import Bus
from recorder import HISTORY_PATH
from recorder.stats import aggregate
from recorder.store import HistoryStore

logger = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
UI_DIR = HERE.parent / "ob_ui"   # extracted static assets (css/js) served under /ui

VERSION = "0.1.0"

# Orchestration structure labels (mirror cofiswarm-agent-registry internal/modes/catalog.go).
MODE_STRUCTURE = {
    "flat": ("fan-out", "All agents run in parallel on the same prompt."),
    "pipeline": ("sequential", "Agents run in order, each stage building on the last."),
    "cascade": ("broadcast→synthesis", "Parallel broadcast, then a synthesizer reduces to one answer."),
    "router": ("routed subset", "A classifier picks a subset; the prompt goes only to those agents."),
}


def component_kind(engine: str) -> str:
    """Classify a component by its engine (mirrors the GUI's groupOf)."""
    if engine == "cofiswarm-mode":
        return "mode"
    if engine in ("llama", "mlx"):
        return "agent"
    return "other"


class ObserverGUI:
    def __init__(self, bus: Bus):
        self._bus = bus
        self._clients: set[web.WebSocketResponse] = set()
        self._roster: dict[str, dict] = {}  # component_id -> last presence payload
        self._store = HistoryStore(HISTORY_PATH)

    async def start_bus(self) -> None:
        await self._bus.subscribe(S.PRESENCE, self._on_presence)
        await self._bus.subscribe(S.ALERT, self._on_alert)
        await self._seed_roster()
        logger.info("Observer GUI subscribed to presence + alert streams")

    async def _seed_roster(self) -> None:
        """Ask the middle man for the current online roster (we may have joined late)."""
        try:
            reply = await self._bus.request(S.ROSTER, S.RosterRequest(), timeout=2.0)
        except Exception:
            logger.error("Roster seed request failed (middle man down?)", exc_info=True)
            return
        for comp in reply.get("components", []):
            cid = comp.get("component_id")
            if cid:
                self._roster[cid] = comp

    async def _on_presence(self, msg, data: dict) -> None:
        cid = data.get("component_id")
        if cid:
            if data.get("status") == "offline":
                self._roster.pop(cid, None)   # don't accumulate stale entries
            else:
                self._roster[cid] = data
        await self._broadcast({"type": "presence", "data": data})

    async def _on_alert(self, msg, data: dict) -> None:
        await self._broadcast({"type": "alert", "data": data})

    async def _broadcast(self, payload: dict) -> None:
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(payload)
            except Exception:
                logger.error("Failed to send to a browser client", exc_info=True)
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(HERE / "index.html")

    async def history(self, request: web.Request) -> web.Response:
        try:
            limit = max(1, min(200, int(request.query.get("limit", 50))))
        except ValueError:
            limit = 50
        return web.json_response(self._store.tail(limit))

    async def stats(self, request: web.Request) -> web.Response:
        return web.json_response(aggregate(self._store.tail(1000)))

    async def roles(self, request: web.Request) -> web.Response:
        """Agent roles (system prompts) + the distinct model server-groups, for the role×model grid."""
        base = Path(os.environ.get(
            "COFISWARM_AGENTS_DIR",
            str(Path.home() / "cofiswarm/repos/cofiswarm-agent-registry/data/agents"),
        ))
        roles, groups = [], {}
        for path in sorted(base.glob("*.json")):
            try:
                d = json.loads(path.read_text())
            except Exception:
                logger.error("bad agent json %s", path, exc_info=True)
                continue
            name = d.get("name") or d.get("agent_id")
            if not name:
                continue
            sg = d.get("server_group") or ""
            roles.append({"name": name, "server_group": sg, "system_prompt": d.get("system_prompt", "")})
            groups.setdefault(sg, name)  # first agent of a group = its representative target
        models = [{"server_group": g, "representative": rep} for g, rep in sorted(groups.items()) if g]
        return web.json_response({"roles": roles, "models": models})

    async def modes(self, request: web.Request) -> web.Response:
        """Orchestration → agent mapping: proxy the agent-registry's per-mode roster.

        The registry (`GET /api/modes/{name}/agents`) is the source of truth for which agents
        a mode drives; we attach each mode's structure label (mirrors the registry catalog).
        Degrades per-mode: an unreachable roster yields `agents: []` rather than failing all.
        """
        base = os.environ.get("COFISWARM_REGISTRY_URL", "http://127.0.0.1:8012").rstrip("/")
        out: dict = {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as sess:
            for name, (structure, desc) in MODE_STRUCTURE.items():
                entry = {"agents": [], "structure": structure, "description": desc}
                try:
                    async with sess.get(f"{base}/api/modes/{name}/agents") as resp:
                        resp.raise_for_status()
                        data = await resp.json(content_type=None)  # registry sends text/plain
                    entry["agents"] = data.get("agents", []) or []
                    for k in ("synthesizer", "order", "max_select"):
                        if data.get(k) is not None:
                            entry[k] = data[k]
                except Exception:
                    logger.error("mode roster fetch failed for %s", name, exc_info=True)
                out[name] = entry
        return web.json_response(out)

    async def status(self, request: web.Request) -> web.Response:
        """Swarm status derived from the live bus roster (go-forward /api/swarm/status).

        The Observer holds presence for every announced component, so it answers
        "what's online" without owning business logic — the middle man stays the coordinator.
        """
        counts = {"agent": 0, "mode": 0, "other": 0, "total": 0}
        components = []
        for cid, d in self._roster.items():
            info = d.get("info") or {}
            engine = info.get("engine", "?")
            kind = component_kind(engine)
            counts[kind] += 1
            counts["total"] += 1
            components.append({
                "component_id": cid,
                "name": info.get("name") or cid,
                "status": d.get("status", "online"),
                "kind": kind,
                "engine": engine,
                "server_group": info.get("server_group"),
            })
        components.sort(key=lambda c: (c["kind"], c["name"]))
        return web.json_response({
            "version": VERSION,
            "online": counts["total"],
            "counts": counts,
            "components": components,
        })

    async def version(self, request: web.Request) -> web.Response:
        return web.json_response({"service": "observer", "version": VERSION})

    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._clients.add(ws)
        await ws.send_json({"type": "snapshot", "data": list(self._roster.values())})
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_client_msg(ws, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    logger.error("WS connection error: %s", ws.exception())
        finally:
            self._clients.discard(ws)
        return ws

    async def _handle_client_msg(self, ws: web.WebSocketResponse, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except Exception:
            logger.error("Bad client message: %s", raw, exc_info=True)
            return
        action = payload.get("action")
        if action == "prompt":
            await self._dispatch_prompt(ws, payload.get("model", ""), payload.get("prompt", ""),
                                        payload.get("session_id"), payload.get("system"),
                                        payload.get("label"))
        elif action == "cancel":
            rid = payload.get("request_id")
            if rid:
                await self._bus.publish(S.CANCEL, S.Cancel(request_id=rid))

    async def _dispatch_prompt(self, ws: web.WebSocketResponse, model: str, prompt: str,
                               session_id: str | None = None, system: str | None = None,
                               label: str | None = None) -> None:
        rid = uuid.uuid4().hex
        holder: dict = {}

        async def on_token(msg, data: dict) -> None:
            try:
                await ws.send_json({"type": "token", "data": data})
            except Exception:
                logger.error("Failed to forward token to browser", exc_info=True)
            if data.get("done") and holder.get("sub"):
                await holder["sub"].unsubscribe()

        holder["sub"] = await self._bus.subscribe(S.tokens_subject(rid), on_token)
        # label lets the browser tag a lane "role@model" (the role×model grid).
        await ws.send_json({"type": "start", "data": {"request_id": rid, "model": label or model}})
        req = S.InferRequest(request_id=rid, model=model, prompt=prompt, stream=True,
                             session_id=session_id, system=system)
        asyncio.create_task(self._send_request(req))

    async def _send_request(self, req: S.InferRequest) -> None:
        try:
            await self._bus.request(S.REQUEST, req, timeout=120.0)
        except Exception:
            # Failure is already surfaced via alert + terminal token by the middle man.
            logger.error("GUI dispatch failed for model %s", req.model, exc_info=True)


def build_app(servers: str = "nats://127.0.0.1:4222") -> web.Application:
    bus = Bus(servers=servers, name="observer-gui")
    gui = ObserverGUI(bus)
    app = web.Application()
    app.router.add_get("/", gui.index)
    app.router.add_get("/ws", gui.ws)
    app.router.add_get("/history", gui.history)
    app.router.add_get("/stats", gui.stats)
    app.router.add_get("/roles", gui.roles)
    app.router.add_get("/modes", gui.modes)
    app.router.add_get("/status", gui.status)
    app.router.add_get("/api/swarm/status", gui.status)  # legacy coordinator path
    app.router.add_get("/version", gui.version)
    app.router.add_get("/api/version", gui.version)       # legacy coordinator path
    app.router.add_static("/ui/", UI_DIR, name="ui")      # extracted css/js (ob_ui/)

    async def _startup(_app):
        await bus.connect()
        await gui.start_bus()

    async def _cleanup(_app):
        await bus.close()

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app
