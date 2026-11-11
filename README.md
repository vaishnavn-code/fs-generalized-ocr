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

## Version 4: PDFs with scanned images, logos and image blocks

Earlier versions ran OCR only when a page had fewer than 50 characters of embedded text, and OCR returned plain
text without positions. Pages that mixed real text with a logo, a pasted scan or a stamp therefore lost whatever
was inside the image, and fully scanned pages skipped the layout rules. Version 4 changes this:

- **One word list per page.** Native text, full-page OCR and OCR of individual image regions are all converted to
  positioned words, so the same field, totals and line-item logic runs on every page.
- **Image regions on digital pages are OCR'd** (`source: "digital+ocr"`). Images under 40 x 10 pt are ignored, and an
  image with 30+ native words on top of it is treated as a background.
- **OCR at 300 dpi with table rule lines removed first**, which recovers column headings and totals that touch a grid line.
- **Line items are rebuilt from word positions** when no ruled table is found (borderless layouts, scans, pasted tables).
- **Nothing is dropped.** Each text segment is in `extracted_fields` / `all_fields`, in a table, or in `unmapped_text`;
  `coverage` reports the counts. Repeated labels are kept as `gstin`, `gstin_2`, ...
- **Validation needs independent evidence**: totals reconcile, item rows match totals, or amount in words matches the total.
  A taxable amount derived from total minus tax no longer produces `passed` on its own.
- Line items carry `unit_price`, `taxable_amount`, `gst_amount`, `gross_amount` and `amount_basis`
  (`taxable`, `gross` or `unknown`); the original `rate` and `amount` keys are still present.

- **Fields that were on the page but not in the response** are now returned: `cgst` / `sgst` / `igst` from the tax
  summary, the HSN-wise `tax_summary` table (with `CGST` / `SGST` over `Rate` / `Amt` headings joined into
  `cgst_rate`, `cgst_amt`, ...), amount in words and other headings whose text sits on the lines below
  (`Description:`, `Terms & Conditions:`), `supplier_gstin`, `buyer`, `buyer_gstin`, `total_quantity`, and per-item
  `unit`, `taxable_amount`, `gst_amount`, `gst_rate_percent`.
- A graphic logo, signature or seal that yields only low-confidence OCR fragments is ignored (mean confidence below 70).

`numpy` is a new dependency (`pip install -r requirements.txt`).

### Tests

```powershell
python -m pytest tests -v
python -m tests.make_samples tests\samples     # writes the synthetic sample PDFs if you want to look at them
python tests\test_invoice_samples.py <folder-with-the-four-real-invoices>
```

The pytest suite uses synthetic invoices with fictional parties in three layouts, each as digital, fully scanned and
digital-with-image-blocks. It does not replace a run against real documents.

### Known gaps

- Tax-summary tables on scanned pages are returned as `unmapped_text`, not as a named table.
- Skewed, rotated, photographed or low-resolution scans are not corrected before OCR.
- Tesseract reads English only (`lang='eng'`); the rupee sign is usually dropped, amounts are unaffected.
- Supplier and buyer detection is heuristic; check `all_fields` when `sender` looks wrong.

## Response layout for tax invoices

`extracted_fields` is grouped, so every value sits under the thing it describes:

| Section | Keys |
|---|---|
| `invoice_details` | `invoice_number`, `invoice_date`, `place_of_supply`, and when present `due_date`, `po_number`, `order_date`, `payment_terms`, `e_way_bill_no`, `irn`, ... |
| `supplier` | `name`, `address`, `gstin`, `phone`, `email`, `state`, `state_code`, `pan` |
| `buyer` | `name`, `address`, `gstin`, `phone`, `state`, `state_code` |
| `ship_to` | `name`, `address` |
| `totals` | `total_quantity`, `taxable_amount`, `cgst`, `sgst`, `igst`, `total_tax`, `sub_total`, `round_off`, `invoice_total`, `amount_in_words`, `amount_received`, `balance_due` |
| `bank_details` | `bank_name`, `branch`, `account_number`, `ifsc_code`, `account_holder_name` |
| `other_details` | `description`, `terms_and_conditions`, `declaration`, `notes` |
| `additional_fields` | any other label/value pair that was read but has no section yet |

`tables` holds `line_items` (`sr_no`, `description`, `hsn_sac`, `quantity`, `unit`, `unit_price`, `taxable_amount`,
`gst_rate_percent`, `gst_amount`, `total_amount`) and `tax_summary` (one row per HSN/SAC plus the total row).

Supplier and buyer are separated by position: text above the first `Bill To` / `Buyer` heading belongs to the supplier,
text under that heading in its own column belongs to the buyer. That is why a GSTIN or State printed twice ends up in the
right section rather than as `gstin` / `gstin_2`.

Text that is in no section and no table is listed in `unmapped_text`. The raw label/value list, the raw table grids
and the page text are returned only with `include_raw=true`.

Images smaller than 2.5% of the page (logos, seals, signatures) are not sent to OCR.
