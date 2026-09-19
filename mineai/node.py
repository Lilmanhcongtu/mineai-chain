"""FastAPI node: REST API + local explorer. Consensus lives in blockchain.py / consensus.py."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, config, log as mlog
from .blockchain import Blockchain
from .config import COIN_NAME, TICKER
from .crypto import validate_address
from .consensus import ValidationError
from .log import event
from .metrics import render_prometheus
from .p2p.node import P2PConfig, P2PNode
from .util import atomic_to_mai

logger = logging.getLogger("mineai.node")
HEIGHT_RE = re.compile(r"^[0-9]{1,10}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


class BodyLimitMiddleware:
    """Reject request bodies larger than the limit for their path (declared or streamed).
    `max_bytes` applies everywhere except paths listed in `path_limits` (e.g. block submission, which must be able to
    carry a block as large as consensus allows)."""

    def __init__(self, app, max_bytes: int, path_limits: dict[str, int] | None = None):
        self.app, self.max_bytes, self.path_limits = app, max_bytes, path_limits or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.path_limits.get(scope.get("path", ""), self.max_bytes)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            if not declared.isdigit():
                return await _error(400, "bad_request", "invalid Content-Length")(scope, receive, send)
            if int(declared) > limit:
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
                if seen > limit:
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


def _fmt_hashrate(value) -> str:
    if value is None:
        return "n/a"
    for unit in ("H/s", "kH/s", "MH/s", "GH/s", "TH/s"):
        if value < 1000 or unit == "TH/s":
            return f"{value:.1f} {unit}" if unit != "H/s" else f"{value:.0f} H/s"
        value /= 1000
    return f"{value:.1f} TH/s"


def _fmt_time(timestamp) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(timestamp)))


def create_app(chain: Blockchain, *, max_body_bytes: int = config.MAX_BODY_BYTES,
               rate_limit_per_minute: int = config.RATE_LIMIT_PER_MINUTE,
               local_hosts: frozenset[str] = LOCAL_HOSTS, p2p: P2PNode | None = None) -> FastAPI:
    params = chain.params
    app = FastAPI(title=f"MineAI Node ({params.name})", version=__version__,
                  description="Read API, transaction submission and (loopback-only) mining endpoints. "
                              "Versioned under /api/v1; the unversioned /api paths are kept as aliases.")
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
    templates.env.filters.update(mai=atomic_to_mai, hashrate=_fmt_hashrate, when=_fmt_time,
                                 short=lambda h, n=16: (h[:n] + "…") if isinstance(h, str) and len(h) > n else h)
    app.state.chain = chain
    app.state.p2p = p2p
    app.add_middleware(RateLimitMiddleware, per_minute=rate_limit_per_minute)
    # A miner must be able to submit a block as large as consensus allows (JSON adds overhead), or a full mempool
    # could never be mined. Found by the private testnet: 160 pending transactions made a 69 KB block, over the 64 KiB cap.
    block_limit = max(max_body_bytes, params.max_block_bytes * 2)
    app.add_middleware(BodyLimitMiddleware, max_bytes=max_body_bytes,
                       path_limits={"/api/v1/mining/submit": block_limit, "/api/mining/submit": block_limit})

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

    def status_info() -> dict:
        tip = chain.tip()
        supply = chain.minted_supply()
        sync = "p2p_disabled" if p2p is None else p2p.sync_status()
        return {
            "name": COIN_NAME, "ticker": TICKER, "version": __version__,
            "network": params.name, "network_id": params.network_id, "label": params.label,
            "genesis_hash": params.genesis_hash or chain.storage.get_block_by_height(0)["hash"],
            "height": tip["height"], "latest_hash": tip["hash"], "difficulty": chain.expected_difficulty(tip),
            "dynamic_difficulty": params.dynamic_difficulty, "target_spacing": params.target_spacing,
            "total_work": str(chain.tip_work()), "reorgs_since_start": chain.reorg_count,
            "side_blocks": chain.storage.side_count(),
            "estimated_hashrate": chain.estimated_hashrate(),
            "block_reward_atomic": params.block_reward, "block_reward_mai": atomic_to_mai(params.block_reward),
            "minted_supply_atomic": supply, "minted_supply_mai": atomic_to_mai(supply),
            "circulating_supply_mai": atomic_to_mai(supply),
            "max_supply_atomic": params.max_supply, "max_supply_mai": atomic_to_mai(params.max_supply),
            "coinbase_maturity": params.coinbase_maturity,
            "mempool_size": chain.storage.mempool_count(),
            "peer_count": len(p2p.peers) if p2p else 0,
            "p2p_enabled": p2p is not None, "sync_status": sync,
        }

    def classify(query: str) -> dict:
        q = query.strip()
        if HEIGHT_RE.match(q):
            if chain.storage.get_block_by_height(int(q)):
                return {"type": "block", "value": q}
        elif HASH_RE.match(q):
            block = chain.storage.get_block_by_hash(q)
            if block:
                return {"type": "block", "value": block["hash"]}
            if chain.find_transaction(q):
                return {"type": "tx", "value": q}
        elif validate_address(q, params.address_prefix):
            return {"type": "address", "value": q}
        raise HTTPException(404, "nothing found for that search")

    def gauges() -> dict:
        tip = chain.tip()
        return {
            "chain_height": tip["height"], "chain_total_work": int(chain.tip_work()),
            "difficulty": chain.expected_difficulty(tip), "estimated_hashrate": chain.estimated_hashrate(),
            "mempool_transactions": chain.storage.mempool_count(), "side_blocks": chain.storage.side_count(),
            "orphan_blocks": len(chain.orphans), "minted_supply_atomic": chain.minted_supply(),
            "peers": len(p2p.peers) if p2p else 0,
            "synced": None if p2p is None else (1 if p2p.sync_status() == "synced" else 0),
        }

    api = APIRouter()

    # ---- health / status
    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.get("/ready")
    def ready():
        return {"status": "ready", "height": chain.tip()["height"],
                "sync_status": "p2p_disabled" if p2p is None else p2p.sync_status()}

    @api.get("/status")
    def status():
        return status_info()

    @api.get("/tip")
    def tip():
        """Cheapest possible 'has anything changed?' probe (used by miners to drop stale work quickly)."""
        t = chain.tip()
        return {"height": t["height"], "hash": t["hash"]}

    @api.get("/metrics")
    def metrics_json():
        """Read-only operational counters and gauges (JSON). Same data as /metrics."""
        return {"gauges": gauges(), "counters": chain.metrics.snapshot(), "uptime_seconds": chain.metrics.uptime(),
                "network": params.name, "version": __version__}

    @api.get("/fee")
    def fee():
        """Fee information for wallets: consensus minimum, default, and what pending transactions currently pay."""
        return {"min_fee_atomic": params.min_fee, "default_fee_atomic": params.default_fee,
                "mempool_median_fee_atomic": chain.storage.mempool_fee_median(),
                "mempool_size": chain.storage.mempool_count()}

    @api.get("/search")
    def search(q: str = Query(..., min_length=1, max_length=100)):
        return classify(q)

    @api.get("/blocks")
    def blocks(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=10_000_000)):
        return chain.storage.list_blocks(limit, offset)

    @api.get("/block/{identifier}")
    def block(identifier: str):
        return lookup_block(identifier)

    @api.get("/tx/{txid}")
    def tx(txid: str):
        if not HASH_RE.match(txid):
            raise ValidationError("txid must be 64 lower-case hex characters", "bad_identifier")
        found = chain.find_transaction(txid)
        if not found:
            raise HTTPException(404, "transaction not found")
        return found

    @api.get("/mempool")
    def mempool(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100_000)):
        return {"count": chain.storage.mempool_count(), "transactions": chain.storage.mempool_list(limit, offset)}

    @api.get("/peers")
    def peers():
        """Read-only. Shows peer addresses and heights only; never anything administrative."""
        return {"count": len(p2p.peers) if p2p else 0,
                "peers": [{k: v for k, v in i.items() if k != "score"} for i in p2p.peer_infos()] if p2p else []}

    @api.get("/account/{address}")
    def account(address: str):
        return chain.account(address)

    @api.get("/address/{address}/transactions")
    def address_transactions(address: str, limit: int = Query(25, ge=1, le=100),
                             offset: int = Query(0, ge=0, le=10_000_000)):
        return chain.address_history(address, limit, offset)

    @api.post("/transactions")
    def submit_transaction(payload: dict = Body(...)):
        accepted = chain.submit_transaction(payload)
        if p2p:
            p2p.announce_tx(accepted)
        return {"accepted": True, "txid": accepted["txid"]}

    # ---- mining (local only)
    @api.get("/mining/template")
    def mining_template(request: Request, address: str = Query(..., max_length=100)):
        require_local(request)
        return chain.mining_template(address)

    @api.post("/mining/submit")
    def mining_submit(request: Request, payload: dict = Body(...)):
        require_local(request)
        accepted = chain.submit_mined_block(payload)
        if p2p:
            p2p.announce_block(accepted)
        return {"accepted": True, "height": accepted["height"], "hash": accepted["hash"]}

    app.include_router(api, prefix="/api/v1")
    app.include_router(api, prefix="/api", include_in_schema=False)      # unversioned aliases (kept for compatibility)

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics_prometheus():
        return render_prometheus(chain.metrics, gauges(), {"version": __version__, "network": params.name})

    # ---- explorer (read-only HTML: no keys, no admin actions)
    PAGE = 20

    def render(request: Request, name: str, **ctx):
        return templates.TemplateResponse(request=request, name=name, context={
            "label": params.label, "network": params.name, "version": __version__, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def explorer(request: Request, page: int = Query(1, ge=1, le=1_000_000)):
        info = status_info()
        rows = chain.storage.list_blocks(PAGE, (page - 1) * PAGE)
        pages = max(1, (info["height"] + 1 + PAGE - 1) // PAGE)
        return render(request, "explorer.html", info=info, blocks=rows, page=page, pages=pages)

    @app.get("/explorer/search")
    def explorer_search(q: str = Query("", max_length=100)):
        try:
            found = classify(q) if q.strip() else None
        except HTTPException:
            found = None
        if not found:
            return HTMLResponse(status_code=404, content=templates.get_template("notfound.html").render(
                label=params.label, network=params.name, version=__version__, query=q))
        return RedirectResponse(f"/explorer/{found['type']}/{found['value']}", status_code=303)

    @app.get("/explorer/block/{identifier}", response_class=HTMLResponse)
    def explorer_block(request: Request, identifier: str):
        b = lookup_block(identifier)
        tip = chain.tip()["height"]
        return render(request, "block.html", block=b, confirmations=tip - b["height"] + 1,
                      has_next=b["height"] < tip, has_prev=b["height"] > 0)

    @app.get("/explorer/tx/{txid}", response_class=HTMLResponse)
    def explorer_tx(request: Request, txid: str):
        if not HASH_RE.match(txid):
            raise HTTPException(404, "transaction not found")
        found = chain.find_transaction(txid)
        if not found:
            raise HTTPException(404, "transaction not found")
        block = chain.storage.get_block_by_height(found["height"]) if found["status"] == "confirmed" else None
        return render(request, "tx.html", found=found, tx=found["transaction"], block=block)

    @app.get("/explorer/address/{address}", response_class=HTMLResponse)
    def explorer_address(request: Request, address: str, page: int = Query(1, ge=1, le=1_000_000)):
        info = chain.account(address)                                    # 400 for an invalid address
        hist = chain.address_history(address, PAGE, (page - 1) * PAGE)
        pages = max(1, (hist["total"] + PAGE - 1) // PAGE)
        return render(request, "address.html", account=info, hist=hist, page=page, pages=pages)

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


def check_main() -> None:
    """`mineai check`: open a database offline and fully re-verify it (hashes, difficulty, work, balances)."""
    import argparse
    from .blockchain import ChainError
    from .storage import StorageError
    ap = argparse.ArgumentParser(description="Verify a MineAI chain database offline (the node must be stopped).")
    ap.add_argument("--network", default=None)
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args()
    params = config.get_params(args.network)
    path = config.db_path_for(params, args.data_dir)
    if not path.exists():
        raise SystemExit(f"no database at {path}")
    try:
        chain = Blockchain(path, params)
        chain.verify_integrity()
    except (ChainError, StorageError) as exc:
        raise SystemExit(f"CHECK FAILED: {exc}")
    print(f"OK: {params.name} database at {path} verified: height {chain.tip()['height']}, "
          f"total work {chain.tip_work()}, minted supply {chain.minted_supply()}")
    chain.close()


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
