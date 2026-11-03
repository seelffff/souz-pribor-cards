import logging
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.generator import generate_card
from app.llm.base import LLMError, LLMProvider, provider_from_env
from app.models import InputError, ProductInput, parse_product_json
from app.render import render_markdown

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx2").setLevel(logging.WARNING)

ROOT = Path(__file__).parent.parent
app = FastAPI(title="Черновики карточек товаров КИП")
app.mount("/examples", StaticFiles(directory=ROOT / "examples" / "input"), name="examples")


@lru_cache
def get_llm() -> LLMProvider:
    return provider_from_env()


@app.exception_handler(InputError)
async def input_error(_: Request, exc: InputError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(LLMError)
async def llm_error(_: Request, exc: LLMError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
def health() -> dict:
    llm = get_llm()
    return {"status": "ok", "provider": llm.name, "model": llm.model}


# Тело читаем сами, а не через ProductInput в сигнатуре: так битый JSON и пустые поля
# получают те же понятные сообщения, что и в CLI.
@app.post(
    "/generate",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ProductInput.model_json_schema()}},
        }
    },
)
async def generate(request: Request, judge: bool = False) -> dict:
    product = parse_product_json(await request.body())
    card = await run_in_threadpool(generate_card, product, get_llm(), judge=judge)
    return {"card": card.model_dump(), "markdown": render_markdown(card)}
