"""Parts of a letter or request that have no 'Label: value' form: the date line, the 'To' block, the subject line.
Also groups whatever text is left into paragraphs, and separates letterhead lines that repeat on every page."""
import re
from difflib import SequenceMatcher
from app.generic_fields import _rows_with_y, _clean
from app.financial import valid_date

_DATE_LINE = re.compile(r'^(?:date[d]?\s*[:\-]?\s*)?(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]+([A-Za-z]{3,9}|\d{1,2})[,\s\-/.]+(\d{2,4})$', re.I)
_DATE_LINE_US = re.compile(r'^(?:date[d]?\s*[:\-]?\s*)?([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$', re.I)
_SUBJECT = re.compile(r'^(subject|sub|re|ref(?:erence)?(?:\s*no\.?)?|kind\s+attn\.?|attention)\s*[-:.]+\s*(.+)$', re.I)
_STOP = re.compile(r'^(?:subject|sub|re|ref|dear|respected|kind\s+attn|attention)\b', re.I)
SALUTATION = re.compile(r'^(?:dear\s+\w+|respected\s+\w+|sir\s*/\s*madam|yours\s+(?:faithfully|sincerely|truly)|thanking\s+you|regards|warm\s+regards)[\s,.]*$', re.I)


def _parse_date(text):
    text = text.strip()
    m = _DATE_LINE.match(text)
    if m:
        day, month, year = m.groups()
        return valid_date(f'{day} {month[:3].title()} {year}') if month.isalpha() else valid_date(f'{day}/{month}/{year}')
    m = _DATE_LINE_US.match(text)
    if m:
        month, day, year = m.groups()
        return valid_date(f'{day} {month[:3].title()} {year}')
    return None


def letter_parts(words, page_height):
    rows = _rows_with_y(words)
    out, consumed = {}, []
    for i, (y, cells, _) in enumerate(rows):
        line = _clean(' '.join(c[2] for c in cells))
        if 'date' not in out and y < page_height * 0.45 and len(cells) == 1:
            parsed = _parse_date(line)
            if parsed:
                out['date'] = parsed
                consumed.append(line)
                continue
        m = _SUBJECT.match(line)
        if m and len(line) <= 300:
            key = 'subject' if m.group(1).lower() in ('subject', 'sub', 're') else re.sub(r'[^a-z]+', '_', m.group(1).lower()).strip('_')
            out.setdefault(key, _clean(m.group(2)))
            consumed.append(line)
            continue
        if 'addressed_to' not in out and len(cells) == 1 and re.fullmatch(r'to\s*[,:]?', line, re.I):
            lines, last = [], y
            for y2, cells2, _ in rows[i + 1:i + 8]:
                text = _clean(' '.join(c[2] for c in cells2))
                if y2 - last > 20 or abs(cells2[0][0] - cells[0][0]) > 20 or _STOP.match(text) or _parse_date(text):
                    break
                if _tidy(text): lines.append(_tidy(text).rstrip(' ,'))
                last = y2
            if lines:
                out['addressed_to'] = lines
                consumed += [line] + lines
    return out, consumed


def _similar(a, b):
    return a == b or (len(a) >= 6 and SequenceMatcher(None, a, b).ratio() >= 0.85)


def _tidy(text):
    # Tick marks, stamps and pen strokes that a scanner's OCR turns into stray symbols.
    return _clean(re.sub(r'[\u2190-\u21ff\u2600-\u27bf\u25a0-\u25ff]', '', text))


def split_letterhead(cells_by_page, page_heights=None):
    """cells_by_page: {page: [{'page','y','x0','text'}, ...]} of text not in any field or table.
    Lines printed on two or more pages (address, phone, website - with small OCR differences) are the letterhead."""
    norm = lambda t: re.sub(r'[^a-z0-9]', '', t.lower())
    pages = sorted(cells_by_page)
    letterhead, rest = [], []
    words = lambda t: set(re.findall(r'[a-z0-9]+', t.lower()))
    for page in pages:
        others = [norm(c['text']) for p in pages if p != page for c in cells_by_page[p]]
        other_words = set().union(*[words(c['text']) for p in pages if p != page for c in cells_by_page[p]]) if len(pages) > 1 else set()
        height = (page_heights or {}).get(page)
        for cell in cells_by_page[page]:
            n = norm(cell['text'])
            # In the top or bottom band of the page, OCR often breaks the same footer into different pieces on
            # each page, so there a line counts as repeated when nearly all of its words occur on another page.
            in_band = height is not None and (cell['y'] >= 0.8 * height or cell['y'] <= 0.12 * height)
            mine = words(cell['text'])
            repeated_words = in_band and mine and len(mine & other_words) >= 0.8 * len(mine)
            if len(pages) > 1 and n and (any(_similar(n, o) for o in others) or repeated_words):
                if not any(_similar(n, norm(l)) for l in letterhead):
                    letterhead.append(cell['text'])
            else:
                rest.append(cell)
    return letterhead, rest


def paragraphs(cells):
    """Join leftover text into reading-order paragraphs: cells on one line, then lines that follow closely."""
    blocks = []
    for cell in sorted(cells, key=lambda c: (c['page'], round(c['y']), c['x0'])):
        last = blocks[-1] if blocks else None
        if last and last['page'] == cell['page'] and cell['y'] - last['y'] <= 16:
            last['text'] += ' ' + cell['text']
            last['y'] = max(last['y'], cell['y'])
        else:
            blocks.append({'page': cell['page'], 'text': cell['text'], 'y': cell['y']})
    return [{'page': b['page'], 'text': _clean(b['text'])} for b in blocks if len(re.sub(r'[\W_]', '', b['text'])) >= 4]