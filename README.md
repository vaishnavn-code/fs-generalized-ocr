# Fourthsignal Hybrid OCR - CPU POC

A runnable **proof of concept**, not a production-ready generalized OCR product. Extracts embedded PDF text directly, uses local Tesseract OCR for low-text pages, extracts **native/digital** PDF tables using pdfplumber, and returns basic financial fields and timings. Mixed pages with both embedded text and scanned regions, scanned table structure, handwriting, and complicated grey/colored layouts are **not yet handled reliably**.

## Windows / PowerShell setup

1. Install Python 3.11.
2. Install Tesseract OCR for Windows (UB Mannheim build): https://github.com/UB-Mannheim/tesseract/wiki . Ensure `tesseract --version` and `tesseract --list-langs` work in a **new terminal** and `eng` is installed. If needed add the Tesseract install directory to PATH.
3. In the extracted project directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m unittest discover -s tests
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Visit http://127.0.0.1:8000/docs and upload a PDF to POST /extract. Or from another PowerShell terminal:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/extract" -F "file=@sample.pdf"
```

Put your own PDF in the project directory (no PDF samples are bundled). Limit: 15 MB, 5 pages. Do not use real confidential financial PDFs in demos unless authorized and redacted.

## Compare performance

```powershell
python compare.py sample.pdf --repeats 5
```

For Docling profiling, **optional** (can be a large install and first run may download models):

```powershell
pip install -r requirements-docling.txt
python profile_docling.py sample.pdf --repeats 3
```

This runs four configurations (full, no OCR, no tables, minimal) and writes `docling_benchmark.json`. `full` uses FAST table mode to aid diagnosis; reproduce your real current Docling settings separately for a fair baseline. The script measures converter creation, first conversion and warm conversions, not internal OCR/render/table stage timings.

## API output

`status`, `page_count`, `pages` (source and extracted text), `fields` (loan amount, interest rate, first date), `tables` (digital pages only), `warnings`, `timings_ms`, `total_ms`, `request_ms`.

## Known limitations and next steps

- The current `<50 chars` per-page heuristic can misclassify short digital pages and mixed-content pages. Next: inspect image area and text coverage, then OCR image regions selectively.
- Scanned pages return OCR text, **not reconstructed table cells**. Next: detect rows/columns using OpenCV and word bounding boxes; benchmark PaddleOCR for difficult layouts.
- Regex field extraction is a basic demonstration and can miss labels, currency variants, or values split across lines.
- Tesseract's per-page timeout is 20 seconds; some scanned PDFs may still be slow or fail. No latency SLA is implied.
- CPU performance depends on hardware, scan quality, DPI and concurrency. Compare on the same documents and check correctness, not speed alone.
- The server is local-only, with no authentication or persistence. Add secure upload handling, per-page pixel limits, resource isolation, monitoring, caching, and authentication before production.

## Version 2: structured financial extraction

The `/extract` response now contains a `financial` object with invoice number, ISO date,
supplier, normalized line items, taxable amount, GST, invoice total, and validation checks.
The original `pages`, `fields`, `tables`, and `timings_ms` remain available.

Run the regression tests with the four original PDF samples in a local folder:

```powershell
python -m tests.test_invoice_samples "C:\path\to\your\pdfs"
```

For the Tally/RM-style invoices, `taxable_amount_source=derived_from_total_and_tax` means
the taxable amount was computed, not independently read from the tax summary.
The current invoice parser is a POC: it does not guarantee correctness on unseen layouts,
scanned tables, or all financial document types. The OCR fallback remains Tesseract.

Restart the FastAPI server after replacing the files, then test `POST /extract` again.
