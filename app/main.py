import time
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from app.extractor import extract_pdf

app = FastAPI(title='Fourthsignal Hybrid OCR POC', version='0.1.0')
MAX_BYTES = 15 * 1024 * 1024

@app.get('/health')
def health():
    return {'status': 'healthy'}

@app.post('/extract')
async def extract(file: UploadFile = File(...)):
    started = time.perf_counter()
    try:
        data = await file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, 'PDF exceeds 15 MB')
        if not data.startswith(b'%PDF-'):
            raise HTTPException(400, 'Expected PDF file')
        result = await run_in_threadpool(extract_pdf, data)
        result['request_ms'] = round((time.perf_counter() - started) * 1000, 2)
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, f'Extraction failed: {type(exc).__name__}') from exc
    finally:
        await file.close()
