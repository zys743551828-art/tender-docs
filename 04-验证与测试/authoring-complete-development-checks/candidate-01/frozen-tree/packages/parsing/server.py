"""Loopback authenticated PDF parser; local bytes only, no external document calls."""

from __future__ import annotations

import argparse
import hmac
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from packages.parsing.core import MAX_BYTES, LocalParser, ParseError

parser: LocalParser | None = None
lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    global parser
    if len(os.environ.get("PARSING_RUNTIME_TOKEN", "")) < 32:
        raise RuntimeError("PARSING_RUNTIME_TOKEN_REQUIRED")
    parser = LocalParser()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def private_runtime(request: Request, call_next):
    token = os.environ.get("PARSING_RUNTIME_TOKEN", "")
    if not token or not hmac.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + token
    ):
        return JSONResponse({"code": "PARSING_UNAUTHORIZED"}, status_code=401)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/identity")
def identity():
    if parser is None:
        return JSONResponse({"code": "PARSING_NOT_READY"}, status_code=503)
    return {
        "protocol_version": 1,
        "identity": parser.identity,
        "parser_digest": parser.parser_digest,
        "config_digest": parser.config_digest,
    }


def compute(data, expected_digest):
    if not lock.acquire(blocking=False):
        return JSONResponse({"code": "PARSING_BUSY"}, status_code=429)
    try:
        if parser is None:
            return JSONResponse({"code": "PARSING_NOT_READY"}, status_code=503)
        return parser.parse(data, expected_digest)
    except ParseError as error:
        return JSONResponse({"code": str(error)}, status_code=422)
    except Exception:
        return JSONResponse({"code": "PARSING_FAILED"}, status_code=503)
    finally:
        lock.release()


@app.post("/parse")
async def parse(request: Request):
    try:
        size = int(request.headers.get("content-length", "-1"))
    except ValueError:
        size = -1
    if not 0 < size <= MAX_BYTES:
        return JSONResponse({"code": "PARSING_INPUT_TOO_LARGE"}, status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > size or len(body) > MAX_BYTES:
            return JSONResponse({"code": "PARSING_INPUT_TOO_LARGE"}, status_code=413)
    if len(body) != size:
        return JSONResponse({"code": "PARSING_INPUT_SIZE_MISMATCH"}, status_code=422)
    expected = request.headers.get("x-source-sha256")
    if not expected or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        return JSONResponse({"code": "PARSING_SOURCE_DIGEST_REQUIRED"}, status_code=422)
    return await run_in_threadpool(compute, bytes(body), expected)


def main():
    import uvicorn

    arguments = argparse.ArgumentParser()
    arguments.add_argument("--port", type=int, default=8812)
    args = arguments.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, workers=1)


if __name__ == "__main__":
    main()
