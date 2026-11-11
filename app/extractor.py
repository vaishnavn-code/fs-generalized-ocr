"""Hybrid native-text/OCR extractor. Scanned table cells are not reconstructed in this POC."""
import io
import re
import time
import pymupdf as fitz
import pdfplumber
import pytesseract
from PIL import Image
from app.generic_fields import key_values_from_words, key_values_from_text, supplier_from_signature
from app.financial import extract_metadata, extract_items, extract_totals, normalize_table, identify_table, validate, totals_from_tables, extract_from_text

MAX_PAGES = 5


def _configure_tesseract():
    """Locate tesseract.exe: TESSERACT_CMD env var, PATH, then common Windows install folders."""
    import os, shutil
    candidates = [
        os.environ.get('TESSERACT_CMD'),
        shutil.which('tesseract'),
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe'),
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return


_configure_tesseract()


def fields_from_text(text):
    expressions = {
        'loan_amount': r'(?:loan amount|sanctioned amount|principal amount)\s*[:\-]?\s*(?:Rs\.?|INR|₹)?\s*([\d,]+(?:\.\d+)?)',
        'interest_rate_percent': r'(?:interest rate|rate of interest)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*%',
        'date': r'\b(\d{1,2}[-/]\d{1,2}[-/]\d{4})\b',
    }
    return {key: (match.group(1) if (match := re.search(pattern, text, re.I)) else None) for key, pattern in expressions.items()}


def extract_pdf(pdf_bytes, dpi=150):
    started = time.perf_counter()
    pages, tables, timings = [], [], {'native_ms': 0.0, 'ocr_ms': 0.0, 'tables_ms': 0.0}
    metadata = {}
    totals = {}
    key_values = []
    signature_supplier = None
    with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
        if doc.needs_pass:
            raise ValueError('Password-protected PDFs are not supported')
        if len(doc) > MAX_PAGES:
            raise ValueError(f'POC supports at most {MAX_PAGES} pages')
        if not len(doc):
            raise ValueError('PDF has no pages')
        for number, page in enumerate(doc, 1):
            if number == 1:
                metadata = extract_metadata(page)
                totals = extract_totals(page)
            t = time.perf_counter()
            native = page.get_text('text', sort=True)
            word_count = len(page.get_text('words'))
            timings['native_ms'] += (time.perf_counter() - t) * 1000
            text, source = native, 'digital'
            if len(native.strip()) < 50:
                t = time.perf_counter()
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes('png')))
                try:
                    text = pytesseract.image_to_string(image, lang='eng', config='--psm 3', timeout=20)
                except pytesseract.TesseractNotFoundError as exc:
                    raise ValueError('Tesseract OCR is not installed or not found; set TESSERACT_CMD to tesseract.exe') from exc
                source = 'ocr'
                timings['ocr_ms'] += (time.perf_counter() - t) * 1000
            if source == 'digital':
                signature_supplier = supplier_from_signature(page.get_text('words')) or signature_supplier
            key_values += key_values_from_words(page.get_text('words'), number) if source == 'digital' else key_values_from_text(text, number)
            pages.append({'page': number, 'source': source, 'native_word_count': word_count, 'text': text})
    t = time.perf_counter()
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
        for number, page in enumerate(doc.pages, 1):
            if pages[number - 1]['source'] == 'digital':
                for table in page.extract_tables():
                    normalized = normalize_table(table)
                    if normalized: tables.append({'page': number, 'type': identify_table(normalized), 'rows': normalized})
    timings['tables_ms'] = (time.perf_counter() - t) * 1000
    items = extract_items(tables)
    ocr_warnings = []
    ocr_pages = [p['text'] for p in pages if p['source'] == 'ocr']
    if ocr_pages:
        o_meta, o_totals, o_items = extract_from_text(chr(10).join(ocr_pages))
        metadata = {k: metadata.get(k) or o_meta.get(k) for k in o_meta}
        totals = {**o_totals, **totals}
        if not items and o_items:
            items = o_items
        ocr_warnings.append('Invoice fields on scanned pages were parsed from OCR text and should be verified')
    if signature_supplier:
        metadata = {**metadata, 'supplier': signature_supplier}
    financial = {**metadata, **totals_from_tables(tables, totals)}
    financial['items'] = items
    financial['item_count'] = len(items)
    financial['gst_amount'] = financial.get('gst_amount') or (round(financial['cgst'] + financial['sgst'], 2) if 'cgst' in financial and 'sgst' in financial else financial.get('igst'))
    if 'taxable_amount' not in financial and financial.get('invoice_total') is not None and financial.get('gst_amount') is not None:
        financial['taxable_amount'] = round(financial['invoice_total'] - financial['gst_amount'] - (financial.get('round_off') or 0), 2)
        financial['taxable_amount_source'] = 'derived_from_total_and_tax'
    financial['validation'] = validate(financial, items)
    return {
        'financial': financial, 'key_value_pairs': key_values,
        'status': 'completed', 'page_count': len(pages), 'pages': pages,
        'fields': {**fields_from_text('\n'.join(p['text'] for p in pages)), 'date': financial.get('invoice_date')},
        'tables': tables, 'warnings': (['Scanned table cell reconstruction is not implemented in this POC'] if ocr_pages else []) + ocr_warnings,
        'timings_ms': {k: round(v, 2) for k, v in timings.items()},
        'total_ms': round((time.perf_counter() - started) * 1000, 2),
    }
