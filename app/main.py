"""FastAPI entry point. Structured response by default, raw data optional."""
import time
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from app.extractor import extract_pdf
from app.response_builder import build_response

app = FastAPI(title="Fourthsignal Hybrid OCR POC", version="0.4.0")
MAX_BYTES = 15 * 1024 * 1024


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/extract")
async def extract(file: UploadFile = File(...), include_raw: bool = False):
    started = time.perf_counter()
    try:
        data = await file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "PDF exceeds 15 MB")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "Expected PDF file")
        result = await run_in_threadpool(extract_pdf, data)
        response = build_response(result, file.filename, include_raw=include_raw)
        response["data"]["processing"]["request_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return response
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, f"Extraction failed: {type(exc).__name__}") from exc
    finally:
        await file.close()