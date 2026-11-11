"""Hybrid extractor. Every page becomes one list of positioned words, whatever its origin:
native PDF text, OCR of a fully scanned page, or OCR of image regions (logos, pasted scans, stamps)
on an otherwise digital page. All field/table logic then runs on that single word list."""
import io
import re
import time
import numpy as np
import pymupdf as fitz
import pdfplumber
import pytesseract
from PIL import Image
from app.generic_fields import key_values_from_words, supplier_from_signature, _rows_from_words, _cells_from_words, unmapped_cells
from app.financial import (extract_metadata, extract_items, extract_totals, normalize_table, identify_table, validate,
                           totals_from_tables, extract_from_text, items_from_words, metadata_from_key_values, finalize_items, tax_summaries, tax_summary_from_words, _column_key)
from app.parties import extract_parties, reference_grid
from app.generic_fields import BOILERPLATE

MAX_PAGES = 5
MIN_NATIVE_CHARS = 50      # below this a page is treated as fully scanned
OCR_DPI = 300              # 150 dpi loses small print (GSTIN, account numbers)
MIN_IMAGE_W, MIN_IMAGE_H = 40, 10   # points; smaller images are icons/rules, not text
MIN_IMAGE_AREA = 0.025     # share of the page; smaller images are logos, seals and signatures - OCR on them costs time and returns noise
BACKGROUND_WORDS = 30      # an image with this many native words on top of it is a background, not a scan
MIN_CONF = 35
REGION_MIN_CONF = 70     # mean confidence an image region needs before its OCR text is kept


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


def remove_rules(image, min_run=70):
    """Whiten long horizontal/vertical strokes (table grid lines, box borders). Tesseract drops or misreads
    words that touch a rule - on gridded invoices that is exactly the column headings and totals."""
    gray = np.asarray(image.convert('L'))
    dark = gray < 140

    def long_runs(d):
        if d.shape[1] <= min_run:
            return np.zeros(d.shape, dtype=bool)
        c = np.cumsum(np.pad(d, ((0, 0), (1, 0))), axis=1, dtype=np.int32)
        starts = np.zeros(d.shape, dtype=np.int32)
        starts[:, :d.shape[1] - min_run + 1] = (c[:, min_run:] - c[:, :-min_run]) == min_run
        cs = np.cumsum(np.pad(starts, ((0, 0), (1, 0))), axis=1)
        idx = np.arange(d.shape[1])
        return (cs[:, idx + 1] - cs[:, np.maximum(idx - min_run + 1, 0)]) > 0

    mask = long_runs(dark) | long_runs(dark.T).T
    cleaned = gray.copy()
    cleaned[mask] = 255
    return Image.fromarray(cleaned)


def ocr_words(page, clip=None, dpi=OCR_DPI, psm=6, line_base=0):
    """OCR a page (or one region of it) and return words in PyMuPDF 'words' format, in page points.
    Returns (words, mean_confidence)."""
    clip = fitz.Rect(clip) if clip is not None else page.rect
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    image = remove_rules(Image.open(io.BytesIO(pix.tobytes('png'))), min_run=max(40, int(70 * dpi / 300)))
    try:
        data = pytesseract.image_to_data(image, lang='eng', config=f'--psm {psm}', timeout=60, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as exc:
        raise ValueError('Tesseract OCR is not installed or not found; set TESSERACT_CMD to tesseract.exe') from exc
    words, confs, line_ids = [], [], {}
    for i, raw in enumerate(data['text']):
        text = (raw or '').strip().strip('|[]').strip()   # table rules are often read as | [ ]
        try:
            conf = float(data['conf'][i])
        except (TypeError, ValueError):
            conf = -1
        if not text or conf < MIN_CONF or not (re.search(r'[A-Za-z0-9₹%]', text) or text in (':', '&', '#', '-', '/', '@')):
            continue
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        line_id = line_ids.setdefault(key, len(line_ids))
        x0 = clip.x0 + data['left'][i] / zoom
        y0 = clip.y0 + data['top'][i] / zoom
        words.append((x0, y0, x0 + data['width'][i] / zoom, y0 + data['height'][i] / zoom, text, 100000 + line_base + line_id, 0, len(words)))
        confs.append(conf)
    # Words of one OCR line get the line's common top/bottom, so later row grouping is not thrown off by
    # per-word box jitter (descenders, punctuation, bold text).
    by_line = {}
    for w in words:
        by_line.setdefault(w[5], []).append(w)
    aligned = []
    for group in by_line.values():
        tops, bottoms = sorted(w[1] for w in group), sorted(w[3] for w in group)
        y0, y1 = tops[len(tops) // 2], bottoms[len(bottoms) // 2]
        aligned += [(w[0], y0, w[2], y1, *w[4:]) for w in group]
    aligned.sort(key=lambda w: w[7])
    return aligned, (round(sum(confs) / len(confs), 1) if confs else None)


def image_regions(page, native):
    """Image areas that may hold text the PDF text layer does not have (logo, pasted scan, stamp, signature block)."""
    regions = []
    for info in page.get_image_info():
        rect = fitz.Rect(info['bbox']) & page.rect
        if rect.is_empty or rect.width < MIN_IMAGE_W or rect.height < MIN_IMAGE_H:
            continue
        if rect.width * rect.height < MIN_IMAGE_AREA * page.rect.width * page.rect.height:
            continue
        inside = sum(1 for w in native if rect.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)))
        if inside >= BACKGROUND_WORDS:
            continue  # watermark/background behind real text
        if any(r.contains(rect) for r in regions):
            continue
        regions = [r for r in regions if not rect.contains(r)] + [rect]
    return regions


def _overlaps_native(word, native):
    cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
    return any(n[0] - 1 <= cx <= n[2] + 1 and n[1] - 1 <= cy <= n[3] + 1 for n in native)


def page_words(page, dpi=OCR_DPI):
    """Returns (words, source, info). source: digital | ocr | digital+ocr."""
    native = page.get_text('words')
    native_chars = sum(len(w[4]) for w in native)
    info = {'native_word_count': len(native), 'ocr_word_count': 0, 'ocr_regions': 0, 'ocr_confidence': None}
    if native_chars < MIN_NATIVE_CHARS:
        words, conf = ocr_words(page, dpi=dpi)
        words = [w for w in words if not _overlaps_native(w, native)] + list(native)
        info.update(ocr_word_count=len(words) - len(native), ocr_regions=1, ocr_confidence=conf)
        return words, 'ocr', info
    regions = image_regions(page, native)
    extra, confs = [], []
    for idx, rect in enumerate(regions):
        found, conf = ocr_words(page, clip=rect, dpi=dpi, line_base=(idx + 1) * 10000)
        found = [w for w in found if not _overlaps_native(w, native) and (len(w[4]) >= 2 or w[4] in ':&#')]
        # A graphic logo, signature or seal produces a few low-confidence fragments; that is noise, not text.
        if found and conf is not None and conf >= REGION_MIN_CONF:
            extra += found
            confs.append(conf)
    if extra:
        info.update(ocr_word_count=len(extra), ocr_regions=len(regions), ocr_confidence=round(sum(c for c in confs if c) / max(1, len(confs)), 1))
        return list(native) + extra, 'digital+ocr', info
    return list(native), 'digital', info


def extract_pdf(pdf_bytes, dpi=OCR_DPI):
    started = time.perf_counter()
    pages, tables, timings = [], [], {'native_ms': 0.0, 'ocr_ms': 0.0, 'tables_ms': 0.0}
    metadata, totals, key_values, warnings = {}, {}, [], []
    signature_supplier = None
    words_by_page = {}
    with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
        if doc.needs_pass:
            raise ValueError('Password-protected PDFs are not supported')
        if len(doc) > MAX_PAGES:
            raise ValueError(f'POC supports at most {MAX_PAGES} pages')
        if not len(doc):
            raise ValueError('PDF has no pages')
        for number, page in enumerate(doc, 1):
            t = time.perf_counter()
            words, source, info = page_words(page, dpi)
            elapsed = (time.perf_counter() - t) * 1000
            timings['ocr_ms' if source != 'digital' else 'native_ms'] += elapsed
            words_by_page[number] = words
            text = page.get_text('text', sort=True) if source == 'digital' else '\n'.join(_rows_from_words(words))
            if number == 1:
                metadata = extract_metadata(words, page.rect.width, page.rect.height, text)
                totals = extract_totals(words)
            signature_supplier = supplier_from_signature(words) or signature_supplier
            key_values += key_values_from_words(words, number)
            pages.append({'page': number, 'source': source, **info, 'text': text})
    t = time.perf_counter()
    digital_pages = [p['page'] for p in pages if p['source'] != 'ocr']
    if digital_pages:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
            for number in digital_pages:
                for table in doc.pages[number - 1].extract_tables():
                    normalized = normalize_table(table)
                    if normalized: tables.append({'page': number, 'type': identify_table(normalized), 'rows': normalized})
    timings['tables_ms'] = (time.perf_counter() - t) * 1000

    items = extract_items(tables)
    if not items:
        # No ruled item table found (borderless layout, scanned page, or table pasted as an image): rebuild from word positions.
        for number, words in words_by_page.items():
            found = items_from_words(words, number)
            if found:
                items += found
                if next(p for p in pages if p['page'] == number)['source'] != 'digital':
                    warnings.append(f'Line items on page {number} were rebuilt from OCR word positions and should be verified')
    metadata = metadata_from_key_values(metadata, key_values)
    ocr_pages = [p for p in pages if p['source'] != 'digital']
    if ocr_pages:
        o_meta, o_totals, o_items = extract_from_text('\n'.join(p['text'] for p in ocr_pages))
        metadata = {k: metadata.get(k) or o_meta.get(k) for k in {**o_meta, **metadata}}
        totals = {**o_totals, **totals}
        if not items and o_items:
            items = o_items
        for p in ocr_pages:
            what = 'the whole page' if p['source'] == 'ocr' else f"{p['ocr_regions']} image region(s)"
            warnings.append(f"Page {p['page']}: text was read by OCR from {what} (mean confidence {p['ocr_confidence']}); verify OCR-derived values")
    if signature_supplier:
        metadata = {**metadata, 'supplier': signature_supplier}
    financial = {**metadata, **totals_from_tables(tables, totals)}
    party = extract_parties(words_by_page[1], financial.get('supplier'))
    financial['parties'] = party
    financial['supplier_gstin'] = party['supplier'].get('gstin')
    financial['buyer'] = party['buyer'].get('name')
    financial['buyer_gstin'] = party['buyer'].get('gstin')
    grid, grid_labels = reference_grid(words_by_page[1])
    financial['reference_grid'] = grid
    summaries = tax_summaries(tables)
    if not summaries:
        for number, words in words_by_page.items():
            summaries += tax_summary_from_words(words, number)
    for summary in summaries:   # CGST / SGST / IGST totals from the tax summary's TOTAL row
        for row in summary['rows']:
            if str(row.get('hsn_sac', '')).lower() != 'total': continue
            if isinstance(row.get('taxable_amount'), float) and financial.get('taxable_amount') is None:
                financial['taxable_amount'] = row['taxable_amount']   # printed on the document, so not a derived figure
            if isinstance(row.get('total_tax'), float) and financial.get('gst_amount') is None:
                financial['gst_amount'] = row['total_tax']
            for tax in ('cgst', 'sgst', 'igst'):
                value = next((v for k, v in row.items() if tax in k and 'rate' not in k and isinstance(v, float)), None)
                if value is not None and financial.get(tax) is None: financial[tax] = value
    quantities = [t['total_quantity'] for t in tables if t.get('total_quantity') is not None]
    if quantities: financial['total_quantity'] = quantities[0]
    # A unit of measure followed by a price ('Pac  ₹ 303.39') is a table cell pair, not a labelled field.
    units = {str(i.get('unit')).lower() for i in items if i.get('unit')}
    key_values = [kv for kv in key_values if kv['label'].lower() not in units]
    # Second line of a two-line column heading sitting over the first data row ('Unit(₹)' over '₹ 2,500.00').
    if items:
        key_values = [kv for kv in key_values if not (re.search(r'\(.*\)$', kv['label']) and _column_key(kv['label']) and re.fullmatch(r'[₹Rs.INR\s-]*[\d,]+(?:\.\d+)?', kv['value']))]
    financial['gst_amount'] = financial.get('gst_amount') or (round(financial['cgst'] + financial['sgst'], 2) if financial.get('cgst') is not None and financial.get('sgst') is not None else financial.get('igst'))
    if financial.get('taxable_amount') is None and financial.get('invoice_total') is not None and financial.get('gst_amount') is not None:
        financial['taxable_amount'] = round(financial['invoice_total'] - financial['gst_amount'] - (financial.get('round_off') or 0), 2)
        financial['taxable_amount_source'] = 'derived_from_total_and_tax'
    items = finalize_items(items, financial)
    financial['items'] = items
    financial['item_count'] = len(items)
    financial['validation'] = validate(financial, items, key_values)

    # Only what the response actually returns counts as 'mapped': a raw grid that is replaced by invoice_items /
    # tax_summary is not returned, so its other cells must be covered by a field or be listed as unmapped.
    returned = [{'page': s['page'], 'rows': [list(r.keys()) + list(r.values()) for r in s['rows']]} for s in summaries]
    if not items and not summaries:
        returned += tables   # non-invoice documents: detected tables are returned as they are
    party_text = ' '.join(str(v) for group in ('supplier', 'buyer', 'ship_to') for v in party[group].values())
    party_text += ' ' + ' '.join(grid_labels) + ' ' + ' '.join(str(v) for v in grid.values() if v)
    if party['supplier'].get('name_as_printed'): party_text += ' for'   # signature line 'for <company>'
    if party['supplier'].get('msme_udyam_no'): party_text += ' msme'
    returned.append({'page': 1, 'rows': [[party_text]]})
    unmapped = []
    for number, words in words_by_page.items():
        unmapped += unmapped_cells(words, number, key_values, returned, financial)
    all_cells = [c for words in words_by_page.values() for row in _cells_from_words(words) for c in row]
    total_cells = len(all_cells)
    form_wording = sum(1 for c in all_cells if BOILERPLATE.match(c[2].strip()) or (c[2].strip().endswith(':') and len(c[2]) <= 45))
    return {
        'financial': financial, 'key_value_pairs': key_values,
        'status': 'completed', 'page_count': len(pages), 'pages': pages,
        'fields': {**fields_from_text('\n'.join(p['text'] for p in pages)), 'date': financial.get('invoice_date')},
        'tables': tables, 'tax_summaries': summaries, 'warnings': warnings,
        'unmapped_text': unmapped,
        'coverage': {'text_segments': total_cells, 'mapped_to_fields_or_tables': total_cells - len(unmapped), 'returned_as_unmapped_text': len(unmapped),
                     'of_which_headings_and_form_wording': form_wording},
        'timings_ms': {k: round(v, 2) for k, v in timings.items()},
        'total_ms': round((time.perf_counter() - started) * 1000, 2),
    }