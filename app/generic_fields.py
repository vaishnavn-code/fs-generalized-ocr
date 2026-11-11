"""Generic label/value extraction. Captures every labelled value on a page so no field is dropped.
No supplier- or template-specific rules."""
import re

GAP = 8  # horizontal gap (pt) treated as a column break
ROW_TOL = 3.0  # vertical tolerance (pt) for words on the same row
_LABEL = r"[A-Z][A-Za-z0-9/&.()#' %-]{0,34}?"
_PAIR = re.compile(r"(" + _LABEL + r")\s*(?<!\d):\s*(.*?)(?=\s+" + _LABEL + r"\s*(?<!\d):(?!\d)|$)")
_VALUE_ONLY = re.compile(r"^(?:(?:₹|Rs\.?|INR)\s*-?\s*[\d,]+(?:\.\d+)?|-?[\d,]*\d\.\d{2})\s*%?$")
_UNITS = {'nos', 'pcs', 'pc', 'kg', 'set', 'pair', 'unit', 'units', 'each', 'no', 'qty'}


def _rows_from_words(words):
    """Group PyMuPDF words into visual rows; columns separated by large gaps become 3 spaces."""
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        if rows and abs(rows[-1]['y'] - yc) <= ROW_TOL:
            rows[-1]['words'].append(w)
        else:
            rows.append({'y': yc, 'words': [w]})
    out = []
    for row in rows:
        ws = sorted(row['words'], key=lambda w: w[0])
        text, prev = '', None
        for w in ws:
            if prev is not None:
                text += '   ' if w[0] - prev[2] > GAP else ' '
            text += w[4]
            prev = w
        out.append(text)
    return out


def _clean(s):
    return re.sub(r'\s+', ' ', s or '').strip(' ,;|')


def _pairs_from_line(line):
    pairs = []
    cells = [c.strip() for c in re.split(r'\s{3,}|\s\|\s', line) if c.strip()]
    # 'Label' ':' 'value' cells (colon in its own column) -> 'Label: value'
    tmp = []
    for c in cells:
        if c == ':' and tmp:
            tmp[-1] += ':'
            continue
        if c.startswith(':') and tmp:
            tmp[-1] += ':'; c = c.lstrip(': ')
        tmp.append(c)
    # A cell ending in ':' takes the next cell as its value, unless that cell is itself a label (heading row).
    merged = []
    for c in tmp:
        if merged and merged[-1].endswith(':') and ':' not in c:
            merged[-1] = merged[-1] + ' ' + c
        else:
            merged.append(c)
    for i, seg in enumerate(merged):
        found = False
        for m in _PAIR.finditer(seg):
            label, value = _clean(m.group(1)), _clean(m.group(2))
            if label and value and len(value) <= 200 and len(label.split()) <= 4:
                pairs.append((label, value)); found = True
        if not found and ':' not in seg and i + 1 < len(merged):
            nxt = _clean(merged[i + 1])
            label = _clean(seg)
            # A label-only cell followed by a numeric/date cell, e.g. 'Gross value   Rs. 60,200.00'.
            if (2 <= len(label) <= 40 and re.search(r'[A-Za-z]{3}', label) and not re.search(r'\d', label) and label.lower() not in _UNITS
                    and _VALUE_ONLY.match(nxt)):
                pairs.append((label, nxt))
    return pairs


def extract_key_values(lines, page_number):
    seen, results = set(), []
    for line in lines:
        for label, value in _pairs_from_line(line):
            key = (label.lower(), value.lower())
            if key in seen:
                continue
            seen.add(key)
            results.append({'label': label, 'value': value, 'page': page_number})
    return results


def key_values_from_words(words, page_number):
    return extract_key_values(_rows_from_words(words), page_number)


def key_values_from_text(text, page_number):
    return extract_key_values(text.splitlines(), page_number)


# ---- Layout rules for labels above values, party blocks and 'in words' lines ----
def _cells_from_words(words):
    """Rows of cells [(x0, x1, text)]; a new cell starts after a gap wider than GAP."""
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        if rows and abs(rows[-1]['y'] - yc) <= ROW_TOL:
            rows[-1]['words'].append(w)
        else:
            rows.append({'y': yc, 'words': [w]})
    out = []
    for row in rows:
        cells = []
        for w in sorted(row['words'], key=lambda w: w[0]):
            if cells and w[0] - cells[-1][1] <= GAP:
                cells[-1] = (cells[-1][0], w[2], cells[-1][2] + ' ' + w[4])
            else:
                cells.append((w[0], w[2], w[4]))
        out.append(cells)
    return out


def _label_like(text):
    t = text.strip()
    return bool(t) and not re.search(r'\d', t) and not t.endswith(':') and 1 <= len(t.split()) <= 4 and len(t) <= 34 and t[0].isalpha()


def _aligned(label, value):
    overlap = min(label[1], value[1]) - max(label[0], value[0])
    shorter = min(label[1] - label[0], value[1] - value[0])
    return abs(label[0] - value[0]) <= 12 or (shorter > 0 and overlap / shorter >= 0.4)


_PARTY = re.compile(r'^(?:buyer|bill\s*to|ship\s*to|consignee|seller|sold\s*to|billed\s*to|shipped\s*to)\b', re.I)


def layout_key_values(words, page_number):
    rows = _cells_from_words(words)
    results = []
    for i, cells in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else []
        # 1. Label row above value row (e.g. 'Invoice No.   Dated' over 'GST/263/25-26   1-Sep-25').
        if 2 <= len(cells) <= 4 and all(_label_like(c[2]) for c in cells):
            for label in cells:
                if _PARTY.match(label[2].strip()):
                    continue
                for row in rows[i + 1:i + 3]:   # value row may sit one row lower (e.g. a company-name row in between)
                    hit = next((v for v in row if _aligned(label, v) and not v[2].strip().endswith(':')
                                and re.search(r'\d', v[2]) and len(v[2]) <= 60), None)
                    if hit:
                        results.append({'label': _clean(label[2]), 'value': _clean(hit[2]), 'page': page_number})
                        break
        # 2. 'Amount ... (in words)' followed by the words on the next row.
        for c in cells:
            if re.search(r'\bin\s+words\b', c[2], re.I) and ':' not in c[2] and nxt:
                results.append({'label': _clean(re.sub(r'\bE\.?\s*&\s*O\.?E\.?\b', '', c[2])), 'value': _clean(nxt[0][2]), 'page': page_number})
        # 3. Party block: heading cell (Buyer / Bill To / Ship To ...) followed by name + address rows in the same column.
        for c in cells:
            if _PARTY.match(c[2].strip()) and len(c[2]) <= 30:
                lines = []
                for r in rows[i + 1:i + 7]:
                    first = next((x for x in r if abs(x[0] - c[0]) <= 12), None)
                    if first is None or ':' in first[2] or re.match(r'^\s*GSTIN', first[2], re.I):
                        break
                    lines.append(first[2])
                if lines:
                    results.append({'label': _clean(re.sub(r'[:]', '', c[2])), 'value': _clean(', '.join(lines)), 'page': page_number})
    return results


def key_values_from_words(words, page_number):  # noqa: F811  (supersedes the colon-only version above)
    seen, out = set(), []
    for kv in extract_key_values(_rows_from_words(words), page_number) + layout_key_values(words, page_number):
        key = (kv['label'].lower(), kv['value'].lower())
        if key not in seen:
            seen.add(key); out.append(kv)
    return out


def supplier_from_signature(words):
    """Company named in a 'for <Company>' signature line (e.g. 'for RM INFOTECH from 1-Apr-2025')."""
    found = None
    for row in _rows_from_words(words):
        for cell in re.split(r'\s{3,}', row):
            m = re.match(r"^for\s+([A-Za-z][\w&.,' -]{2,70}?)(?:\s+from\s+\d.*)?[:.]*$", cell.strip(), re.I)
            if m and not re.search(r'(?:recipient|customer|office|details)', m.group(1), re.I):
                found = m.group(1).strip(' .,')
    return found
