"""Private loopback-only CPU model runtime; never downloads assets or logs request bodies."""

from __future__ import annotations

import argparse
import hmac
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from packages.rag.lexical import ChineseLexical
from packages.rag.local_models import BgeDense, BgeReranker

MAX_BODY = 2 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]
lock = threading.Lock()
dense: BgeDense | None = None
reranker: BgeReranker | None = None
lexical: ChineseLexical | None = None


@asynccontextmanager
async def lifespan(app):
    global dense, lexical
    import torch

    if len(os.environ.get("RAG_RUNTIME_TOKEN", "")) < 32:
        raise RuntimeError("Private RAG runtime token missing")
    torch.set_num_threads(2)
    directory = Path(os.environ.get("RAG_ASSET_DIRECTORY", str(ROOT / ".local/models")))
    dense = BgeDense(directory / "dense")
    lexical = ChineseLexical()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def private_runtime(request: Request, call_next):
    token = os.environ.get("RAG_RUNTIME_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    if not token or not hmac.compare_digest(supplied, "Bearer " + token):
        return JSONResponse({"code": "RAG_RUNTIME_UNAUTHORIZED"}, status_code=401)
    if request.method == "POST":
        try:
            size = int(request.headers.get("content-length", "-1"))
        except ValueError:
            size = -1
        if not 0 <= size <= MAX_BODY:
            return JSONResponse({"code": "RAG_INPUT_TOO_LARGE"}, status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    return JSONResponse({"code": "RAG_INVALID_INPUT"}, status_code=422)


@app.exception_handler(ValueError)
async def invalid_model_input(request, error):
    return JSONResponse({"code": "RAG_MODEL_INPUT_INVALID"}, status_code=422)


@app.exception_handler(Exception)
async def unavailable(request, error):
    return JSONResponse({"code": "RAG_RUNTIME_UNAVAILABLE"}, status_code=503)


class Texts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texts: list[str] = Field(min_length=1, max_length=16)


class Text(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=200000)


class Pairs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=10000)
    texts: list[str] = Field(min_length=1, max_length=20)


@app.get("/identity")
def identity():
    if dense is None:
        raise RuntimeError("Runtime is not ready")
    return {
        "identity": dense.identity,
        "dimensions": 1024,
        "device": "cpu",
        "lexical_dictionary_digest": lexical.dictionary_digest if lexical else None,
    }


@app.post("/lexical")
def lexemes(body: Text):
    with lock:
        if lexical is None:
            raise RuntimeError("Runtime is not ready")
        return {"lexemes": lexical.lexemes(body.text)}


@app.post("/tokenize")
def tokenize(body: Text):
    with lock:
        if dense is None:
            raise RuntimeError("Runtime is not ready")
        return {"offsets": dense.offsets(body.text)}


@app.post("/embed")
def embed(body: Texts):
    with lock:
        if dense is None:
            raise RuntimeError("Runtime is not ready")
        return {"identity": dense.identity, "vectors": dense.embed(body.texts)}


@app.post("/rerank")
def rerank(body: Pairs):
    global reranker
    with lock:
        if reranker is None:
            directory = Path(os.environ.get("RAG_ASSET_DIRECTORY", str(ROOT / ".local/models")))
            reranker = BgeReranker(directory / "reranker")
        return {"scores": reranker.rank(body.query, body.texts)}


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8810)
    arguments = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=arguments.port, access_log=False, log_level="critical")


if __name__ == "__main__":
    main()
