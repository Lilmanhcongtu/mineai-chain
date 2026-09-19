"""FastAPI node: REST API + local explorer. Consensus lives in blockchain.py / consensus.py."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, config, log as mlog
from .blockchain import Blockchain
from .config import COIN_NAME, TICKER
from .consensus import ValidationError
from .log import event
from .p2p.node import P2PConfig, P2PNode
from .util import atomic_to_mai

logger = logging.getLogger("mineai.node")
HEIGHT_RE = re.compile(r"^[0-9]{1,10}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


class BodyLimitMiddleware:
    """Reject request bodies larger than `max_bytes` (declared or streamed)."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            if not declared.isdigit():
                return await _error(400, "bad_request", "invalid Content-Length")(scope, receive, send)
            if int(declared) > self.max_bytes:
                return await _error(413, "too_large", "request body too large")(scope, receive, send)
        seen = 0
        exceeded = False

        class _TooLarge(Exception):
            pass

        async def limited():
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded_send(message):
            if not exceeded:                 # the framework may convert our abort into a 400; drop that
                await send(message)

        try:
            await self.app(scope, limited, guarded_send)
        except _TooLarge:
            pass
        if exceeded:
            await _error(413, "too_large", "request body too large")(scope, receive, send)


class RateLimitMiddleware:
    """Per-client sliding-window limiter (in-memory; adequate for a single-node prototype)."""

    def __init__(self, app, per_minute: int):
        self.app, self.per_minute = app, per_minute
        self.hits: dict[str, deque] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.per_minute <= 0:
            return await self.app(scope, receive, send)
        client = (scope.get("client") or ("unknown", 0))[0]
        now = time.monotonic()
        window = self.hits.setdefault(client, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return await _error(429, "rate_limited", "too many requests")(scope, receive, send)
        window.append(now)
        if len(self.hits) > 10_000:                      # bound memory
            for key in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                self.hits.pop(key, None)
        await self.app(scope, receive, send)


def create_app(chain: Blockchain, *, max_body_bytes: int = config.MAX_BODY_BYTES,
               rate_limit_per_minute: int = config.RATE_LIMIT_PER_MINUTE,
               local_hosts: frozenset[str] = LOCAL_HOSTS, p2p: P2PNode | None = None) -> FastAPI:
    params = chain.params
    app = FastAPI(title=f"MineAI Node ({params.name})", version=__version__)
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
    app.state.chain = chain
    app.state.p2p = p2p
    app.add_middleware(RateLimitMiddleware, per_minute=rate_limit_per_minute)
    app.add_middleware(BodyLimitMiddleware, max_bytes=max_body_bytes)

    # ---- uniform error responses (never leak stack traces)
    @app.exception_handler(ValidationError)
    async def _validation(_: Request, exc: ValidationError):
        return _error(400, exc.code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        return _error(422, "invalid_request", "request body or parameters are malformed")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        event(logger, logging.ERROR, "unhandled_exception", type=type(exc).__name__)
        return _error(500, "internal_error", "internal error")

    def require_local(request: Request):
        host = request.client.host if request.client else ""
        if host not in local_hosts:
            raise HTTPException(403, "mining endpoints are only available from the local machine")

    def lookup_block(identifier: str) -> dict:
        if HEIGHT_RE.match(identifier):
            block = chain.storage.get_block_by_height(int(identifier))
        elif HASH_RE.match(identifier):
            block = chain.storage.get_block_by_hash(identifier)
        else:
            raise ValidationError("identifier must be a block height or a 64-character hex hash", "bad_identifier")
        if block is None:
            raise HTTPException(404, "block not found")
        return block

    # ---- health / status
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/ready")
    def ready():
        return {"status": "ready", "height": chain.tip()["height"]}

    @app.get("/api/status")
    def status():
        tip = chain.tip()
        supply = chain.minted_supply()
        return {
            "name": COIN_NAME, "ticker": TICKER, "version": __version__,
            "network": params.name, "network_id": params.network_id, "label": params.label,
            "genesis_hash": params.genesis_hash or chain.storage.get_block_by_height(0)["hash"],
            "height": tip["height"], "latest_hash": tip["hash"], "difficulty": chain.expected_difficulty(tip),
            "dynamic_difficulty": params.dynamic_difficulty, "target_spacing": params.target_spacing,
            "total_work": str(chain.tip_work()), "reorgs_since_start": chain.reorg_count,
            "side_blocks": chain.storage.side_count(),
            "block_reward_atomic": params.block_reward, "block_reward_mai": atomic_to_mai(params.block_reward),
            "minted_supply_atomic": supply, "minted_supply_mai": atomic_to_mai(supply),
            "max_supply_atomic": params.max_supply, "max_supply_mai": atomic_to_mai(params.max_supply),
            "coinbase_maturity": params.coinbase_maturity,
            "mempool_size": chain.storage.mempool_count(),
            "peer_count": len(p2p.peers) if p2p else 0,
            "p2p_enabled": p2p is not None,
        }

    @app.get("/api/peers")
    def peers():
        """Read-only. Shows peer addresses and heights only; never anything administrative."""
        return {"count": len(p2p.peers) if p2p else 0,
                "peers": [{k: v for k, v in i.items() if k != "score"} for i in p2p.peer_infos()] if p2p else []}

    @app.get("/api/blocks")
    def blocks(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=10_000_000)):
        return chain.storage.list_blocks(limit, offset)

    @app.get("/api/block/{identifier}")
    def block(identifier: str):
        return lookup_block(identifier)

    @app.get("/api/tx/{txid}")
    def tx(txid: str):
        if not HASH_RE.match(txid):
            raise ValidationError("txid must be 64 lower-case hex characters", "bad_identifier")
        found = chain.find_transaction(txid)
        if not found:
            raise HTTPException(404, "transaction not found")
        return found

    @app.get("/api/mempool")
    def mempool(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100_000)):
        return {"count": chain.storage.mempool_count(), "transactions": chain.storage.mempool_list(limit, offset)}

    @app.get("/api/account/{address}")
    def account(address: str):
        return chain.account(address)

    @app.post("/api/transactions")
    def submit_transaction(payload: dict = Body(...)):
        accepted = chain.submit_transaction(payload)
        if p2p:
            p2p.announce_tx(accepted)
        return {"accepted": True, "txid": accepted["txid"]}

    # ---- mining (local only)
    @app.get("/api/mining/template")
    def mining_template(request: Request, address: str = Query(..., max_length=100)):
        require_local(request)
        return chain.mining_template(address)

    @app.post("/api/mining/submit")
    def mining_submit(request: Request, payload: dict = Body(...)):
        require_local(request)
        accepted = chain.submit_mined_block(payload)
        if p2p:
            p2p.announce_block(accepted)
        return {"accepted": True, "height": accepted["height"], "hash": accepted["hash"]}

    # ---- explorer
    @app.get("/", response_class=HTMLResponse)
    def explorer(request: Request):
        return templates.TemplateResponse(request=request, name="explorer.html", context={
            "latest": chain.tip(), "supply": atomic_to_mai(chain.minted_supply()),
            "max_supply": atomic_to_mai(params.max_supply), "mempool": chain.storage.mempool_count(),
            "blocks": chain.storage.list_blocks(30), "atomic_to_mai": atomic_to_mai,
            "label": params.label, "network": params.name, "version": __version__})

    @app.get("/explorer/block/{identifier}", response_class=HTMLResponse)
    def explorer_block(request: Request, identifier: str):
        return templates.TemplateResponse(request=request, name="block.html", context={
            "block": lookup_block(identifier), "atomic_to_mai": atomic_to_mai, "label": params.label})

    return app


async def _serve(chain: Blockchain, params, host: str, port: int) -> None:
    p2p = None
    if config.P2P_ENABLED:
        cfg = P2PConfig(host=config.P2P_HOST, port=config.P2P_PORT or params.default_p2p_port,
                        seeds=config.SEEDS, max_peers=config.MAX_PEERS)
        p2p = P2PNode(chain, cfg)
        await p2p.start()
    server = uvicorn.Server(uvicorn.Config(create_app(chain, p2p=p2p), host=host, port=port, log_config=None))
    try:
        await server.serve()
    finally:
        if p2p:
            await p2p.stop()


def main() -> None:
    mlog.configure(config.LOG_LEVEL)
    params = config.get_params()
    port = config.PORT or params.default_port
    if config.HOST not in LOCAL_HOSTS:
        logger.warning("binding to a non-local address: mining endpoints stay loopback-only, but the "
                       "rest of the API is exposed. This is prototype software.")
    if config.P2P_HOST not in LOCAL_HOSTS:
        logger.warning("P2P is listening on a non-local address. Prototype software: do not expose it.")
    chain = Blockchain(config.db_path_for(params), params)
    event(logger, logging.INFO, "node_starting", network=params.name, host=config.HOST, port=port)
    asyncio.run(_serve(chain, params, config.HOST, port))


if __name__ == "__main__":
    main()
