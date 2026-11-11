"""Who is who on an invoice: supplier block, buyer block, ship-to block.
The page is cut into zones by position (above the first 'Bill To' / 'Buyer' heading = supplier; under that heading,
in its own column = buyer) and the generic label/value rules are run on each zone separately, so a label that
occurs twice (GSTIN, State, Phone) is attributed to the right party instead of becoming gstin / gstin_2."""
import re
from app.generic_fields import _rows_with_y, key_values_from_words, _clean

GSTIN = re.compile(r'\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b')
BUYER_HEAD = re.compile(r"^(?:buyer(?!['’]?s?\s*(?:order|ref|po)\b)|bill(?:ed)?\s*to|sold\s*to|customer(?!\s*(?:care|support|id|no))|invoice\s*to)\b", re.I)
SHIP_HEAD = re.compile(r'^(?:ship(?:ped)?\s*to|consignee|deliver(?:y|ed)?\s*(?:to|address))\b', re.I)
PARTY_KEYS = [('gstin', r'gstin|gst\s*(?:no|in)\b'), ('pan', r'\bpan\b'), ('cin', r'\bcin\b'), ('email', r'e-?mail'),
              ('phone', r'phone|mobile|contact|\btel\b|\bmob\b'), ('state_code', r'^(?:state\s*)?code$'),
              ('state', r'^state(?:\s*name)?$'), ('website', r'website|\bweb\b')]
ITEM_HEAD = re.compile(r'^(?:#|sl\.?\s*no\.?|s\.?\s*no\.?|item(?: name)?|description(?: of goods)?|particulars?|hsn\s*/?\s*sac|qty|quantity)$', re.I)


def _party_fields(kvs):
    out, used = {}, []
    for kv in kvs:
        label = kv['label'].strip()
        for key, pattern in PARTY_KEYS:
            if key not in out and re.search(pattern, label, re.I) and len(label) <= 30:
                value = kv['value']
                if key == 'gstin':
                    m = GSTIN.search(value.upper())
                    if not m: break
                    value = m.group()
                out[key] = value
                used.append((kv['label'].lower(), kv['value'].lower()))
                break
    return out, used


def _find_heading(rows, pattern):
    for y, cells, _ in rows:
        for j, c in enumerate(cells):
            if pattern.match(c[2].strip()) and len(c[2]) <= 30:
                right = cells[j + 1][0] if j + 1 < len(cells) else None
                return {'y': y, 'x0': c[0], 'right': right}
    return None


def _zone(words, heading, end_y):
    right = heading['right'] - 2 if heading['right'] is not None else float('inf')
    return [w for w in words if heading['y'] - 4 <= (w[1] + w[3]) / 2 < end_y and heading['x0'] - 6 <= w[0] < right]


def extract_parties(words, supplier_name=None):
    rows = _rows_with_y(words)
    buyer_head = _find_heading(rows, BUYER_HEAD)
    ship_head = _find_heading(rows, SHIP_HEAD)
    items_y = next((y for y, cells, _ in rows if sum(bool(ITEM_HEAD.match(c[2].strip())) for c in cells) >= 2), None)
    result = {'supplier': {}, 'buyer': {}, 'ship_to': {}, 'consumed': []}

    def block(heading, other):
        end = min([v for v in (items_y, heading['y'] + 150) if v is not None and v > heading['y']])
        if other and other['y'] > heading['y'] + 4 and abs(other['x0'] - heading['x0']) < 40:
            end = min(end, other['y'] - 2)
        kvs = key_values_from_words(_zone(words, heading, end), 1)
        fields, used = _party_fields(kvs)
        party = next((kv for kv in kvs if kv.get('lines')), None)
        if party:
            used.append((party['label'].lower(), party['value'].lower()))
        return fields, used, party

    if buyer_head:
        fields, used, party = block(buyer_head, ship_head)
        if party:
            result['buyer']['name'] = party['lines'][0]
            if party['lines'][1:]: result['buyer']['address'] = _clean(', '.join(party['lines'][1:]))
        result['buyer'].update(fields)
        result['consumed'] += used
    if ship_head and (not buyer_head or abs(ship_head['y'] - buyer_head['y']) > 4 or ship_head['x0'] != buyer_head['x0']):
        fields, used, party = block(ship_head, buyer_head)
        if party:
            lines = party['lines']
            # A ship-to block is either 'name + address lines' or just the address.
            if len(lines) > 1 and not re.search(r'\d', lines[0]):
                result['ship_to']['name'] = lines[0]
                lines = lines[1:]
            result['ship_to']['address'] = _clean(', '.join(lines))
        result['ship_to'].update(fields)
        result['consumed'] += used

    top = min([h['y'] for h in (buyer_head, ship_head) if h] or [float('inf')])
    limit = top - 4 if top != float('inf') else (items_y or 250)
    supplier_words = [w for w in words if (w[1] + w[3]) / 2 < limit]
    fields, used = _party_fields(key_values_from_words(supplier_words, 1))
    if supplier_name:
        result['supplier']['name'] = supplier_name
        # Address = the lines right under the supplier name, in the same column, until the first labelled line.
        target = re.sub(r'[^a-z0-9]', '', supplier_name.lower())
        srows = _rows_with_y(supplier_words)
        for i, (y, cells, _) in enumerate(srows):
            hit = next((c for c in cells if target and re.sub(r'[^a-z0-9]', '', c[2].lower()).startswith(target)), None)
            if not hit:
                continue
            lines = []
            for y2, cells2, _ in srows[i + 1:i + 5]:
                c2 = next((c for c in cells2 if abs(c[0] - hit[0]) <= 12), None)
                if c2 is None or ':' in c2[2] or GSTIN.search(c2[2].upper()) or BUYER_HEAD.match(c2[2]):
                    break
                lines.append(c2[2])
            if lines: result['supplier']['address'] = _clean(', '.join(lines))
            break
    result['supplier'].update(fields)
    result['consumed'] += used
    return result
