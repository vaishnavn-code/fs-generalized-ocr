"""Layout-aware invoice parsing. No supplier-specific templates or external AI."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

AMOUNT = re.compile(r'-?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+\.\d{2})')
DATE = re.compile(r'\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ]\d{2,4})\b', re.I)

def number(value):
    if value is None: return None
    s = str(value).replace(',', '').replace('₹', '').replace('Rs.', '').replace('INR', '').strip()
    match = re.search(r'-?\s*\d+(?:\.\d+)?', s)
    if not match: return None
    try: return float(Decimal(match.group().replace(' ', '')))
    except InvalidOperation: return None

def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()

def valid_date(value):
    if not value: return None
    for fmt in ('%d/%m/%Y','%d-%m-%Y','%d.%m.%Y','%d/%m/%y','%d-%m-%y','%d.%m.%y','%d-%b-%y','%d-%b-%Y','%d %b %Y','%d %b %y','%d-%B-%Y','%d-%B-%y','%d %B %Y','%d %B %y','%Y-%m-%d','%d/%b/%Y','%d/%b/%y'):
        try: return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError: pass
    return None

def normalize_table(rows):
    rows = [list(r) for r in rows if r]
    if not rows: return []
    width = max(map(len, rows))
    rows = [r + [None] * (width-len(r)) for r in rows]
    keep = [i for i in range(width) if any(clean(r[i]) for r in rows)]
    return [[clean(r[i]) or None for i in keep] for r in rows if any(clean(r[i]) for i in keep)]

ITEM_DESC = r'(?:description of (?:goods|services?)|item name|item description|\bdescription\b|\bparticulars?\b|\bitem\b|\bproduct\b)'

def identify_table(rows):
    head = ' '.join(clean(c).lower() for row in rows for c in row if c)
    if re.search(ITEM_DESC, head) and re.search(r'\b(?:quantity|qty|rate|price|amount)\b', head):
        return 'items'
    if 'hsn' in head and ('cgst' in head or 'sgst' in head or 'igst' in head): return 'tax_summary'
    return 'other'

def get_lines(source):
    # Word positions allow label/value pairing even when the PDF's reading order is unusual.
    # `source` is a PyMuPDF page or a list of words (native and/or OCR) in PyMuPDF 'words' format.
    words = source.get_text('words') if hasattr(source, 'get_text') else source
    lines = {}
    for x0,y0,x1,y1,word,block,line,*_ in words:
        lines.setdefault((block,line), []).append((x0,y0,x1,y1,word))
    return [sorted(v,key=lambda w:w[0]) for v in sorted(lines.values(),key=lambda v:min(w[1] for w in v))]

OTHER_DATE = re.compile(r'\b(?:due|order|delivery|ack|expiry|po|challan|lr|supply|dispatch|payment)\b\.?\s*date', re.I)
INVOICE_NO_LABEL = re.compile(r'^(?:(?:tax\s+)?(?:invoice|inv|bill)\s*(?:(?:no|number|num)\b\.?|#)|invoice$)', re.I)
INVOICE_DATE_LABEL = re.compile(r'^(?:invoice\s*date|date\s*of\s*invoice|bill\s*date|dated|date)$', re.I)

def extract_metadata(source, width=None, height=None, text=None):
    """`source` is a PyMuPDF page, or a word list (native and/or OCR) with the page width/height and text."""
    if hasattr(source, 'get_text'):
        width, height, text = source.rect.width, source.rect.height, source.get_text('text', sort=True)
    lines = get_lines(source)
    all_text = text or ''
    invoice_number = None
    invoice_date = None
    # Prefer explicit label/value pairs; do not parse date-looking substrings of invoice IDs.
    for line in lines:
        s = ' '.join(w[4] for w in line)
        m = re.search(r'\b(?:Invoice\s*(?:No\.?|Number|#)|No\s*:)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/.-]*\d[A-Z0-9/.-]*)',s,re.I)
        if m and not invoice_number: invoice_number = m.group(1)
        if OTHER_DATE.search(s) and not re.search(r'invoice\s*date', s, re.I): continue
        m = re.search(r'\b(?:Invoice\s*Date|Date|Dated)\s*[:\-]?\s*('+DATE.pattern+r')',s,re.I)
        if m and not invoice_date: invoice_date = valid_date(m.group(1))
    if not invoice_date:
        # Candidate dates closest to top-right 'Dated' or 'Date' headings.
        candidates=[]
        for line in lines:
            for i,w in enumerate(line):
                if DATE.fullmatch(w[4]) and w[4] not in (invoice_number or ''):
                    d=valid_date(w[4]);
                    if d: candidates.append((w[1],w[0],d))
        if candidates and width and height:
            # Prefer right-side, upper-half date (not supplier founding date).
            right=[c for c in candidates if c[1]>width*0.52 and c[0]<height*0.45]
            if right: invoice_date=min(right,key=lambda c:c[0])[2]
    if not invoice_date:
        m=re.search(r'\bDate\s*:\s*('+DATE.pattern+r')',all_text,re.I)
        if m: invoice_date=valid_date(m.group(1))
    supplier = None
    for line in lines:
        s=clean(' '.join(w[4] for w in line))
        if s and re.search(r'[A-Za-z]{3}', s) and not re.search(r'^(?:tax invoice|invoice|bill to|ship to|original for|duplicate|page\s+\d)', s, re.I) and not re.search(r'\b(?:GSTIN|phone|email|invoice details|date:)\b', s, re.I) and len(s) < 110:
            supplier=s.split('REGD.')[0].strip().rstrip(' .'); break
    if not supplier:
        # Conservative fallback: first non-title line; may require review.
        for line in lines:
            s=clean(' '.join(w[4] for w in line))
            if s and not re.fullmatch('tax invoice',s,re.I): supplier=s;break
    return {'invoice_number':invoice_number,'invoice_date':invoice_date,'supplier':supplier}

def metadata_from_key_values(metadata, key_values):
    """Fill invoice number/date from generic label/value pairs (covers label-above-value grids and OCR pages).
    Replaces the old supplier-specific invoice-number patterns."""
    out = dict(metadata)
    for kv in key_values:
        label, value = kv['label'].strip(), kv['value'].strip()
        if not out.get('invoice_number') and INVOICE_NO_LABEL.match(label):
            m = re.match(r'[A-Z0-9][A-Z0-9/._-]*', value, re.I)
            if m and re.search(r'\d', m.group()) and not valid_date(m.group()): out['invoice_number'] = m.group().rstrip('.')
    for kv in key_values:
        label, value = kv['label'].strip(), kv['value'].strip()
        if not out.get('invoice_date') and INVOICE_DATE_LABEL.match(label):
            m = DATE.search(value)
            d = valid_date(m.group()) if m else None
            if d and m.group() not in (out.get('invoice_number') or ''): out['invoice_date'] = d
    return out

CHARGE = re.compile(r'^(?:output\s+)?(?:cgst|sgst|igst|utgst|gst|cess|tcs|tds|round(?:ed|ing)?\s*off|discount|freight|less\b|add\b|total|sub\s*total|grand\s*total|taxable|tax\b)', re.I)

def extract_items(tables):
    items=[]
    for table in tables:
        if table['type']!='items':continue
        rows=table['rows']
        def is_head(r):
            low=[clean(c).lower() for c in r]
            old=any('hsn' in c for c in low) and any('quantity' in c or 'rate'==c for c in low)
            new=any(re.search(ITEM_DESC,c) and len(c)<40 for c in low) and sum(bool(re.search(r'^(?:quantity|qty|rate|price|amount|total|unit price|taxable)',c)) for c in low)>=2
            return old or new
        head_idx=next((i for i,r in enumerate(rows) if is_head(r)),None)
        if head_idx is None:continue
        header=rows[head_idx]
        def col(*terms):
            for term in terms:
                hit=next((i for i,c in enumerate(header) if term in clean(c).lower()),None)
                if hit is not None:return hit
            return None
        hsn=col('hsn','sac');qty=col('quantity','qty');description=col('item name','description of goods','description','particular','item','product')
        amount=next((i for i in range(len(header)-1,-1,-1) if 'amount' in clean(header[i]).lower() and 'taxable' not in clean(header[i]).lower()),None)
        if amount is None: amount=next((i for i in range(len(header)-1,-1,-1) if clean(header[i]).lower() in ('total','line total','net total','value')),None)
        rate=col('price/','unit price','price')
        if rate is None: rate=next((i for i,c in enumerate(header) if clean(c).lower()=='rate'),None)
        letters=lambda c: re.sub(r'[^a-z ]','',clean(c).lower()).strip()
        gst=next((i for i,c in enumerate(header) if re.fullmatch(r'(?:gst|igst|tax)(?:\s*(?:amount|amt))?',letters(c))),None)
        unit_col=next((i for i,c in enumerate(header) if letters(c) in ('unit','per','uom','units')),None)
        taxable=col('taxable')
        cell=lambda row,i: clean(row[i]) if i is not None and i<len(row) else ''
        for row in rows[head_idx+1:]:
            if any('tax summary' in clean(c).lower() for c in row):break
            first=clean(row[0])
            if first.lower() in ('total','tax summary:') or any(clean(c).lower()=='total' for c in row[:2]):
                if qty is not None and number(cell(row,qty)) is not None: table['total_quantity']=number(cell(row,qty))
                break
            desc=cell(row,description)
            is_item=bool(re.fullmatch(r'\d{1,3}',first)) and hsn is not None and hsn<len(row) and bool(re.fullmatch(r'\d{4,8}',clean(row[hsn])))
            if not is_item:
                # Layouts without serial numbers or HSN codes: a described row with a single amount.
                is_item=bool(desc) and not CHARGE.match(desc) and '\n' not in desc and bool(re.fullmatch(r'(?:₹|Rs\.?|INR)?\s*[\d,]+\.\d{2}',cell(row,amount)))
            if is_item:
                item={'description':desc or None,'hsn_sac':cell(row,hsn) or None,'quantity':number(row[qty]) if qty is not None else None,'rate':number(row[rate]) if rate is not None else None,'amount':number(row[amount]) if amount is not None else None}
                unit=re.search(r'\d\s*([A-Za-z]{2,6})\.?$',cell(row,qty))
                if unit:item['unit']=unit.group(1)
                if unit_col is not None and cell(row,unit_col):item['unit']=cell(row,unit_col)
                if taxable is not None and number(cell(row,taxable)) is not None:item['taxable_amount']=number(cell(row,taxable))
                if gst is not None and cell(row,gst):
                    amt=AMOUNT.search(cell(row,gst));pct=re.search(r'(\d{1,2}(?:\.\d+)?)\s*%',cell(row,gst))
                    if amt:item['gst_amount']=number(amt.group(0))
                    if pct:item['gst_rate_percent']=float(pct.group(1))
                item['page']=table.get('page')
                items.append(item)
            elif items and description is not None and description<len(row):
                extra=clean(row[description])
                if extra and not CHARGE.match(extra) and not any(clean(c).lower() in ('cgst 9%','sgst 9%') for c in row):
                    items[-1]['description']=clean((items[-1]['description'] or '')+' '+extra)
    return items

COLUMN_KEYS = [('description', r'^(?:description(?: of (?:goods|services?))?(?: (?:and|&|/) services?)?|item(?: name| description)?|particulars?|product(?: name)?|services?|goods)$'),
               ('hsn_sac', r'hsn|sac'), ('quantity', r'^(?:qty|quantity)\b'), ('unit', r'^(?:per|unit|uom)$'),
               ('rate', r'^(?:rate|price|unit\s*price|price\s*/\s*unit|mrp|unit rate)\b'), ('taxable_amount', r'taxable'),
               ('gst', r'^(?:gst|igst|cgst|sgst|tax)\b'), ('amount', r'^(?:amount|total|net amount|line total|value)\b')]
STOP_ROW = re.compile(r'^(?:sub\s*-?total|grand\s*total|total|tax\s*summary|amount\s*(?:chargeable|in\s*words)|invoice\s*amount|taxable\s*(?:value|amount)|bank|terms|declaration|e\.?\s*&\s*o)', re.I)

def _column_key(text):
    t = re.sub(r'\(.*?\)', '', clean(text).lower()).strip(' .:')   # 'Unit(₹)' -> 'unit'
    return next((key for key, pattern in COLUMN_KEYS if re.search(pattern, t)), None)

def _word_rows(words, row_tol=3.0, gap=8):
    """Visual rows of cells; each cell is (x0, x1, text, [word boxes])."""
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        if rows and abs(rows[-1][0] - yc) <= row_tol: rows[-1][1].append(w)
        else: rows.append([yc, [w]])
    out = []
    for _, ws in rows:
        cells = []
        for w in sorted(ws, key=lambda w: w[0]):
            if cells and w[0] - cells[-1][1] <= gap:
                cells[-1] = (cells[-1][0], w[2], cells[-1][2] + ' ' + w[4], cells[-1][3] + [w])
            else:
                cells.append((w[0], w[2], w[4], [w]))
        out.append(cells)
    return out

def items_from_words(words, page_number):
    """Rebuild line items from positioned words when there is no ruled table to rely on
    (borderless layouts, scanned pages, tables pasted as images)."""
    items = []
    rows = _word_rows(words)
    for start, cells in enumerate(rows):
        keys = [_column_key(c[2]) for c in cells]
        if len(cells) < 3 or 'description' not in keys or sum(k in ('amount', 'rate', 'quantity', 'taxable_amount') for k in keys) < 2:
            continue
        columns = [(k, c[0], c[1]) for k, c in zip(keys, cells)]
        if keys.count('amount') > 1:  # keep the right-most 'amount/total' column as the line amount
            last = max(i for i, k in enumerate(keys) if k == 'amount')
            columns = [(None if k == 'amount' and i != last else k, a, b) for i, (k, a, b) in enumerate(columns)]
        def column_of(cell):
            x0, x1, text = cell
            numeric = bool(re.fullmatch(r'[₹Rs.INR\s]*-?[\d,]+(?:\.\d+)?\s*(?:%|[A-Za-z]{2,5}\.?)?(?:\s*\(\d+(?:\.\d+)?%\))?', text.strip()))
            best, score = None, None
            for i, (_, a, b) in enumerate(columns):
                overlap = min(x1, b) - max(x0, a)
                dist = -overlap if overlap > 0 else (abs(x1 - b) if numeric else abs(x0 - a)) + 1000
                if score is None or dist < score: best, score = i, dist
            return best
        for cells2 in rows[start + 1:]:
            row_text = clean(' '.join(c[2] for c in cells2))
            if STOP_ROW.match(row_text): break
            got = {}
            pieces = []
            for c in cells2:
                spans = [i for i, (_, a, b) in enumerate(columns) if min(c[1], b) - max(c[0], a) > 0]
                # A cell that runs under two column headings is two values printed close together: split it by word.
                pieces += [(w[0], w[2], w[4]) for w in c[3]] if len(spans) >= 2 else [c[:3]]
            for c in pieces:
                key = columns[column_of(c)][0]
                if key: got[key] = clean((got.get(key, '') + ' ' + c[2]))
            desc = got.get('description', '')
            money = next((got[k] for k in ('amount', 'taxable_amount', 'rate') if AMOUNT.search(got.get(k, ''))), None)
            if desc and money and not CHARGE.match(desc):
                item = {'description': desc, 'hsn_sac': got.get('hsn_sac') or None, 'quantity': number(got.get('quantity')),
                        'rate': number(got.get('rate')), 'amount': number(got.get('amount'))}
                unit = got.get('unit') or (re.search(r'\d\s*([A-Za-z]{2,6})\.?$', got.get('quantity', '')) or [None, None])[1]
                if unit: item['unit'] = unit
                if number(got.get('taxable_amount')) is not None: item['taxable_amount'] = number(got['taxable_amount'])
                if got.get('gst'):
                    amt = AMOUNT.search(got['gst']); pct = re.search(r'(\d{1,2}(?:\.\d+)?)\s*%', got['gst'])
                    if amt: item['gst_amount'] = number(amt.group(0))
                    if pct: item['gst_rate_percent'] = float(pct.group(1))
                item['page'] = page_number
                items.append(item)
            elif items and items[-1]['page'] == page_number and desc and not money and len(got) == 1 and ':' not in desc[-1:] and len(desc) < 90 and not CHARGE.match(desc):
                items[-1]['description'] = clean(items[-1]['description'] + ' ' + desc)
            elif items and not got:
                continue
        if items: break
    return items

def finalize_items(items, fields):
    """Say what each line 'amount' means using evidence from the totals, never by assumption:
    unit_price, taxable_amount, gst_amount and gross_amount are only filled when they can be supported."""
    amounts = [i.get('amount') for i in items]
    basis = 'unknown'
    if items and all(a is not None for a in amounts):
        s = sum(amounts)
        near = lambda target, tol: target is not None and abs(s - target) <= tol
        taxable, gst = fields.get('taxable_amount'), fields.get('gst_amount')
        own_taxable = [i.get('taxable_amount') for i in items]
        if near(taxable, 0.05): basis = 'taxable'
        elif near(fields.get('invoice_total'), max(1.0, abs(fields.get('round_off') or 0) + 0.05)) or (taxable is not None and gst is not None and near(taxable + gst, 0.05)): basis = 'gross'
        elif all(t is not None for t in own_taxable) and all(a >= t for a, t in zip(amounts, own_taxable)) and s > sum(own_taxable): basis = 'gross'
        elif near(fields.get('subtotal'), 0.05): basis = 'taxable'   # 'Sub Total' is before tax on most layouts; checked last because some print it after tax
    out = []
    for item in items:
        row = dict(item)
        row['unit_price'] = row.get('rate')
        row.setdefault('taxable_amount', None); row.setdefault('gst_amount', None); row['gross_amount'] = None
        if basis == 'taxable' and row['taxable_amount'] is None: row['taxable_amount'] = row.get('amount')
        if basis == 'gross': row['gross_amount'] = row.get('amount')
        if row['gst_amount'] is None and row['gross_amount'] is not None and row['taxable_amount'] is not None:
            row['gst_amount'] = round(row['gross_amount'] - row['taxable_amount'], 2)
        if row['taxable_amount'] is None and row['gross_amount'] is not None and row['gst_amount'] is not None:
            row['taxable_amount'] = round(row['gross_amount'] - row['gst_amount'], 2)
        if row['gross_amount'] is None and row['taxable_amount'] is not None and row['gst_amount'] is not None:
            row['gross_amount'] = round(row['taxable_amount'] + row['gst_amount'], 2)
        row['amount_basis'] = basis
        out.append(row)
    return out

def extract_totals(source):
    # Position-based search: label and value must be in the same horizontal region.
    words=source.get_text('words') if hasattr(source,'get_text') else source
    from app.generic_fields import _rows_from_words
    # PDF text lines first; then whole visual rows, which join a label to an amount printed far to its right
    # (borderless totals blocks, OCR output) that the PDF stores as separate lines.
    texts=[' '.join(w[4] for w in line) for line in get_lines(words)]+_rows_from_words(words)
    results={}
    labels={'taxable_amount':r'\bTaxable\s+(?:Value|Amount)\b','round_off':r'\bRound(?:ed|ing)?\s*Off\b','subtotal':r'\bSub\s*-?\s*Total\b'}
    # A tax label counts only when its own amount follows it (optionally after a rate), so a heading row such as
    # 'CGST  SGST  Total Tax' sitting beside an unrelated amount is not mistaken for the tax value.
    tax_line=r'\b%s\b\s*(?:@|:|-)?\s*(?:\d{1,2}(?:\.\d+)?(?:\s*%%|\s+))?\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+\.\d{2})(?!\s*%%)'
    explicit=r'\b(?:Grand\s+Total|Invoice\s+Total|Total\s+Amount|Net\s+(?:Amount\s+)?Payable|Amount\s+Payable|Total\s+Payable|Invoice\s+Value)\b'
    plain_totals=[]
    for s in texts:
        matches=list(AMOUNT.finditer(s))
        if not matches:continue
        last=number(matches[-1].group(0))
        if re.search(explicit,s,re.I) and 'invoice_total' not in results and not re.search(r'in\s+words',s,re.I):
            results['invoice_total']=last
        elif re.search(r'(?<!Sub )(?<!Sub)(?<!Sub-)\bTotal\b(?!\s+Tax)',s,re.I) and not re.search(r'\bSub\s*-?\s*Total\b',s,re.I):
            plain_totals.append(last)
        for tax in ('cgst','sgst','igst'):
            m=re.search(tax_line%tax,s,re.I)
            if m and tax not in results: results[tax]=number(m.group(1))
        for field,pattern in labels.items():
            if field in results or not re.search(pattern,s,re.I):continue
            # Extract rightmost currency/decimal amount on the same line.
            val=last
            if field=='round_off' and '-' in s[max(0,matches[-1].start()-3):matches[-1].start()+1]: val=-abs(val)
            results[field]=val
    # Several rows can say 'Total' (item total, tax-summary total); the invoice total is the largest of them.
    if 'invoice_total' not in results and plain_totals:
        results['invoice_total']=max(plain_totals)
    return results

_UNITS_W = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split())}
_TENS_W = {w: (i + 2) * 10 for i, w in enumerate('twenty thirty forty fifty sixty seventy eighty ninety'.split())}
_TENS_W['fourty'] = 40
_SCALE_W = {'hundred': 100, 'thousand': 1000, 'lakh': 100000, 'lac': 100000, 'lakhs': 100000, 'lacs': 100000, 'million': 1000000, 'crore': 10000000, 'crores': 10000000}

def words_to_number(text):
    """'INR One Thousand Six Hundred Fifty Two Only' -> 1652.0 (Indian and western scales, optional paise). None if unreadable."""
    tokens = re.findall(r'[a-z]+', (text or '').lower().replace('-', ' '))
    def parse(seq):
        total = current = 0; seen = False
        for tok in seq:
            if tok in _UNITS_W: current += _UNITS_W[tok]; seen = True
            elif tok in _TENS_W: current += _TENS_W[tok]; seen = True
            elif tok == 'hundred': current = (current or 1) * 100; seen = True
            elif tok in _SCALE_W: total += (current or 1) * _SCALE_W[tok]; current = 0; seen = True
        return (total + current) if seen else None
    if 'paise' in tokens or 'paisa' in tokens:
        cut = max(i for i, tok in enumerate(tokens) if tok in ('paise', 'paisa'))
        head = tokens[:cut]
        split = max((i for i, tok in enumerate(head) if tok in ('and', 'rupees', 'rupee')), default=None)
        if tokens[cut + 1:] and parse(tokens[cut + 1:]) is not None:   # 'Rupees X and Paise Fifty'
            rupees, paise = parse(head), parse(tokens[cut + 1:])
        elif split is not None:
            rupees, paise = parse(head[:split]), parse(head[split:])
        else:
            rupees, paise = parse(head), None
        if rupees is None: return None
        return round(rupees + (paise or 0) / 100, 2)
    value = parse(tokens)
    return float(value) if value is not None else None

def validate(fields,items,key_values=None):
    """A check is only recorded when it compares two values that were read independently from the document."""
    checks={}
    total=fields.get('invoice_total');taxable=fields.get('taxable_amount');round_off=fields.get('round_off',0) or 0
    gst=fields.get('gst_amount')
    derived=fields.get('taxable_amount_source')=='derived_from_total_and_tax'
    if total is not None and taxable is not None and gst is not None and not derived:
        checks['total_reconciles']=abs(total-(taxable+gst+round_off))<=0.02
    if items and all(i.get('amount') is not None for i in items):
        s=sum(i['amount'] for i in items)
        targets=[t for t in (fields.get('subtotal'),taxable) if t is not None]  # a taxable figure derived from total and tax is still independent of the item rows
        if any(abs(s-t)<=0.05 for t in targets): checks['items_match_totals']=True
        elif total is not None and abs(s-total)<=max(1.0,abs(round_off)+0.05): checks['items_match_totals']=True
        elif targets or total is not None: checks['items_match_totals']=False
    if total is not None:
        for kv in key_values or []:
            if re.search(r'in\s+words',kv['label'],re.I) and not re.search(r'\btax\b',kv['label'],re.I):
                spoken=words_to_number(kv['value'])
                if spoken: checks['amount_in_words_matches_total']=abs(spoken-total)<=0.5; break
    status='failed' if any(v is False for v in checks.values()) else 'passed' if any(v is True for v in checks.values()) else 'not_verified'
    return {'status':status,'checks':checks}

def totals_from_tables(tables, existing):
    result=dict(existing)
    for table in tables:
        rows=table['rows']
        for row in rows:
            cells=[clean(c) for c in row]
            for cell in cells:
                for key,pattern in [('invoice_total',r'\bTotal\s*:\s*₹?\s*([\d,]+\.\d{2})'),('subtotal',r'\bSub\s*Total\s*:\s*₹?\s*([\d,]+\.\d{2})'),('round_off',r'\bRound\s*Off\s*:\s*(-?\s*₹?\s*[\d,]+\.\d{2})')]:
                    m=re.search(pattern,cell,re.I)
                    if m:result[key]=number(m.group(1))
            # RM/Tally invoice totals: label 'Total' and last monetary cell
            if 'invoice_total' not in result and table['type']=='items' and any(c.lower()=='total' for c in cells[:2]):
                values=[number(c) for c in cells if re.search(r'[\d,]+\.\d{2}',c)]
                if values:result['invoice_total']=values[-1]
            # SSPL tax summary totals are a single row with TOTAL and values
            if any(c.lower()=='total' for c in cells[:2]):
                if table['type']=='items' and len([c for c in cells if c])>=4:
                    values=[number(c) for c in cells if re.search(r'[\d,]+\.\d{2}',c)]
                    if len(values)>=3:
                        result.setdefault('taxable_amount',values[-3])
                        result.setdefault('gst_amount',values[-2])
                elif len([c for c in cells if c])>=3:
                    values=[number(c) for c in cells if re.search(r'[\d,]+\.\d{2}',c)]
                    if len(values)>=4:
                        result['taxable_amount']=values[0]
                        result['gst_amount']=values[-1]
                    if len(values)==4 and abs(values[1]+values[2]-values[3])<=0.02:
                        result.setdefault('cgst',values[1]);result.setdefault('sgst',values[2])
                    elif len(values)==3 and abs(values[1]-values[2])<=0.02 and values[0]>values[1]:
                        result.setdefault('taxable_amount',values[0]);result.setdefault('igst',values[1]);result.setdefault('gst_amount',values[2])
            for label in ('CGST','SGST','IGST'):
                if any(re.search(r'\b'+label+r'\b',c,re.I) for c in cells):
                    values=[number(c) for c in cells if re.fullmatch(r'₹?\s*[\d,]+\.\d{2}',c)]
                    if values: result[label.lower()]=values[-1]
    if 'gst_amount' not in result and 'cgst' in result and 'sgst' in result:
        result['gst_amount']=round(result['cgst']+result['sgst'],2)
    return result


def extract_from_text(text):
    """Layout-free fallback for OCR text (scanned invoices). Returns (metadata, totals, items).
    Only labelled/structurally-recognisable values are returned; anything else stays None."""
    meta = {'invoice_number': None, 'invoice_date': None, 'supplier': None}
    totals, items = {}, []
    lines = [clean(l) for l in text.splitlines()]
    lines = [l for l in lines if l]

    # Invoice number + date: first invoice-id-like token (letters + digits + separators) after an 'Invoice No' label.
    label = re.search(r'Invoice\s*(?:No\.?|Number)', text, re.I)
    if label:
        window = text[label.end():label.end() + 300]
        for m in re.finditer(r'\b(?=[A-Z0-9/-]*\d)(?=[A-Z0-9/-]*[A-Z])[A-Z0-9]+(?:[/-][A-Z0-9]+)+\b', window):
            if DATE.fullmatch(m.group()): continue
            meta['invoice_number'] = m.group()
            d = DATE.search(window[m.end():m.end() + 80]) or DATE.search(window)
            meta['invoice_date'] = valid_date(d.group()) if d else None
            break

    # Supplier: 'for <Company>' signature line, else account-holder name.
    for l in lines:
        m = re.match(r'^for\s+([A-Za-z][\w&.,\' -]{2,70})$', l, re.I)
        if m: meta['supplier'] = m.group(1).strip(); break
    if not meta['supplier']:
        m = re.search(r"Holder'?s Name\s*[:=-]\s*([^\n]+)", text, re.I)
        if m: meta['supplier'] = clean(m.group(1))

    amounts = lambda s: [number(a.group(0)) for a in AMOUNT.finditer(s)]
    for l in lines:
        # Tax summary row: Total: taxable cgst sgst total_tax (or taxable igst total_tax)
        if re.match(r'^Total\s*:', l, re.I) and 'taxable_amount' not in totals:
            v = amounts(l)
            if len(v) >= 4 and abs(v[1] + v[2] - v[3]) <= 0.02:
                totals.update(taxable_amount=v[0], cgst=v[1], sgst=v[2], gst_amount=v[3])
            elif len(v) == 3 and abs(v[1] - v[2]) <= 0.02:
                totals.update(taxable_amount=v[0], igst=v[1], gst_amount=v[2])
        # Grand total: a line that is only 'Total' and one amount.
        m = re.match(r'^Total\s*[=:]?\s*(?:₹|Rs\.?)?\s*([\d,]+\.\d{2})$', l, re.I)
        if m and 'invoice_total' not in totals: totals['invoice_total'] = number(m.group(1))
        m = re.match(r'^(CGST|SGST|IGST)\b.*?([\d,]+\.\d{2})$', l, re.I)
        if m and m.group(1).lower() not in totals: totals[m.group(1).lower()] = number(m.group(2))
        m = re.match(r'^Round\s*Off\b.*?(-?\s*[\d,]+\.\d{2})$', l, re.I)
        if m: totals['round_off'] = number(m.group(1))
    # The tax-summary 'Total:' row's own total-tax value must not be mistaken for the invoice total.
    if totals.get('invoice_total') is not None and totals.get('invoice_total') == totals.get('gst_amount'): totals.pop('invoice_total')

    # Line items: '<sl> <description> <HSN/SAC> [gst%] [qty] [rate] <amount>'
    for l in lines:
        m = re.match(r'^(\d{1,3})\s+(.+?)\s+(\d{4,8})\s+(.+)$', l)
        if not m: continue
        tail = m.group(4)
        v = amounts(tail)
        if not v: continue
        pct = re.search(r'(\d{1,2}(?:\.\d+)?)\s*%', tail)
        items.append({'description': m.group(2), 'hsn_sac': m.group(3), 'quantity': None, 'rate': None,
                      'gst_rate_percent': float(pct.group(1)) if pct else None, 'amount': v[-1]})
    return meta, totals, items


def tax_summaries(tables):
    """HSN-wise tax summary as named rows. Billing apps often draw it inside the same grid as the line items,
    with a two-level heading (CGST / SGST over Rate and Amt); both levels are joined into one key."""
    out = []
    for table in tables:
        rows = table['rows']
        head = next((i for i, r in enumerate(rows) if re.search(r'hsn|sac', clean(r[0]).lower())
                     and any(re.search(r'cgst|sgst|igst|taxable|total tax', clean(c).lower()) for c in r[1:])
                     and not any(re.search(ITEM_DESC, clean(c).lower()) for c in r)), None)
        if head is None:
            continue
        top = [clean(c) for c in rows[head]]
        last = max(i for i, c in enumerate(top) if c)
        sub = [clean(c) for c in rows[head + 1]] if head + 1 < len(rows) else []
        has_sub = any(re.search(r'rate|amt|amount', c, re.I) for c in sub[:last + 1]) and not re.fullmatch(r'\d{4,8}|total', clean(sub[0] if sub else ''), re.I)
        names, group = {}, ''
        for i in range(last + 1):
            if top[i]: group = top[i]
            below = sub[i] if has_sub and i < len(sub) else ''
            if top[i] and not below and not (has_sub and any(sub[k] for k in range(i, min(last + 1, len(sub))) if k == i)):
                names[i] = top[i]
            if below: names[i] = f'{group} {below}'
        keyed = {i: re.sub(r'[^a-z0-9]+', '_', n.lower().replace('₹', '')).strip('_') for i, n in names.items()}
        result = []
        for row in rows[head + (2 if has_sub else 1):]:
            first = clean(row[0])
            if not re.fullmatch(r'\d{4,8}|total', first, re.I):
                continue
            entry = {}
            for i, key in keyed.items():
                value = clean(row[i]) if i < len(row) else ''
                if not value: continue
                entry[key] = value if i == 0 else (number(value) if number(value) is not None else value)
            if len(entry) > 1: result.append(entry)
        if result:
            out.append({'page': table.get('page'), 'rows': result, 'source_type': table.get('type')})
    return out
