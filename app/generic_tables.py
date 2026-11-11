"""Layout-independent table detection for any document type.
Works from word positions only, so it finds tables that have no ruled grid (system reports, statements, schedules):
a heading block (one to three lines of column titles) followed by rows whose cells line up in the same columns."""
import re
from app.generic_fields import _rows_with_y, _clean, GAP

_SEPARATOR = re.compile(r'^[\W_]+$')


def _has_digit(text):
    return bool(re.search(r'\d', text))


def _is_header(cells):
    """Column titles: several short cells, mostly words, none of them a 'Label : value' pair."""
    if len(cells) < 3 or any(':' in c[2] for c in cells):
        return False
    numeric = sum(1 for c in cells if re.fullmatch(r'[\d,.\-/:% ]+', c[2].strip()))
    return numeric == 0 and all(len(c[2]) <= 40 for c in cells)


def _is_data(cells, min_cells):
    return len(cells) >= min_cells and any(_has_digit(c[2]) for c in cells) and not any(c[2].strip().endswith(':') for c in cells)


def _columns(data_rows):
    """Merge horizontally overlapping cells of all data rows into column x-ranges."""
    spans = sorted((c[0], c[1]) for cells in data_rows for c in cells)
    columns = []
    for x0, x1 in spans:
        if columns and x0 <= columns[-1][1] + 1:
            columns[-1][1] = max(columns[-1][1], x1)
        else:
            columns.append([x0, x1])
    return columns


def _column_of(x0, x1, columns):
    best, score = 0, None
    for i, (a, b) in enumerate(columns):
        overlap = min(x1, b) - max(x0, a)
        dist = -overlap if overlap > 0 else min(abs(x0 - b), abs(x1 - a)) + 1000
        if score is None or dist < score:
            best, score = i, dist
    return best


def _key(text, index, seen):
    base = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_') or f'column_{index + 1}'
    key, n = base, 1
    while key in seen:
        n += 1
        key = f'{base}_{n}'
    seen.add(key)
    return key


def detect_tables(words, page_number):
    rows = [(y, cells, ws) for y, cells, ws in _rows_with_y(words) if not all(_SEPARATOR.match(c[2]) for c in cells)]
    tables, i = [], 0
    while i < len(rows):
        if not _is_header(rows[i][1]):
            i += 1
            continue
        head = [i]
        while len(head) < 3 and head[-1] + 1 < len(rows) and rows[head[-1] + 1][0] - rows[head[-1]][0] <= 14 \
                and len(rows[head[-1] + 1][1]) >= 2 and not any(':' in c[2] for c in rows[head[-1] + 1][1]) \
                and not _is_data(rows[head[-1] + 1][1], 3):
            head.append(head[-1] + 1)
        width = max(len(rows[h][1]) for h in head)
        data, j = [], head[-1] + 1
        while j < len(rows) and _is_data(rows[j][1], max(3, int(width * 0.6))) and rows[j][0] - rows[j - 1][0] <= 40:
            data.append(j)
            j += 1
        if not data:
            i += 1
            continue
        columns = _columns([rows[d][1] for d in data])
        # Column titles: a title cell that sits over two data columns ('Sr. Reversal Date for') is split by word.
        titles = [[] for _ in columns]
        for h in head:
            for c in rows[h][1]:
                over = [k for k, (a, b) in enumerate(columns) if min(c[1], b) - max(c[0], a) > 0]
                if len(over) >= 2:
                    for w in [w for w in rows[h][2] if c[0] - 0.5 <= w[0] and w[2] <= c[1] + 0.5]:
                        titles[_column_of(w[0], w[2], columns)].append(w[4])
                else:
                    titles[_column_of(c[0], c[1], columns)].append(c[2])
        seen = set()
        keys = [_key(_clean(' '.join(t)), k, seen) for k, t in enumerate(titles)]
        out = []
        for d in data:
            entry = {}
            for c in rows[d][1]:
                k = keys[_column_of(c[0], c[1], columns)]
                entry[k] = _clean((entry.get(k, '') + ' ' + c[2]))
            out.append(entry)
        tables.append({'page': page_number, 'columns': [_clean(' '.join(t)) for t in titles], 'rows': out,
                       'y0': rows[head[0]][0], 'y1': rows[data[-1]][0]})
        i = j
    return tables


def headings(words, limit=5):
    """Title lines at the top of the page: single-cell lines before the first labelled field or table."""
    lines = []
    for y, cells, _ in _rows_with_y(words):
        if all(_SEPARATOR.match(c[2]) for c in cells):
            continue
        from app.letters import _parse_date
        text = cells[0][2].strip()
        if len(cells) != 1 or ':' in text or len(lines) >= limit or _parse_date(text) or re.fullmatch(r'to\s*,?', text, re.I):
            break
        lines.append(_clean(cells[0][2]))
    return lines