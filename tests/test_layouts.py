"""Regression tests on synthetic invoices (fictional parties) in three layouts, each as:
digital, fully scanned, and digital with image blocks (logo/letterhead, pasted scan).
Run: python -m pytest tests/test_layouts.py -v      (OCR cases are skipped if Tesseract is not installed)"""
import shutil
import pytest
import pytesseract
from app.extractor import extract_pdf
from app.response_builder import build_response
from tests.make_samples import make_all

HAS_TESSERACT = bool(shutil.which('tesseract')) or pytesseract.pytesseract.tesseract_cmd != 'tesseract'
ocr = pytest.mark.skipif(not HAS_TESSERACT, reason='Tesseract not installed')

EXPECTED = {
    'tally_style': dict(invoice_number='GST/263/25-26', invoice_date='2025-09-01', invoice_total=1652.0, taxable_amount=1400.0, gst_amount=252.0, item_count=2),
    'billing_app_style': dict(invoice_number='ZVS/25-26/1843', invoice_date='2026-02-02', invoice_total=2950.0, taxable_amount=2500.0, gst_amount=450.0, item_count=1),
    'borderless_style': dict(invoice_number='INV-2026-0042', invoice_date='2026-03-15', invoice_total=11800.0, taxable_amount=10000.0, gst_amount=1800.0, item_count=2),
}
VARIANTS = {
    'tally_style': ['tally_style', pytest.param('tally_style_scanned', marks=ocr), pytest.param('tally_scanned_tables', marks=ocr)],
    'billing_app_style': ['billing_app_style', pytest.param('billing_app_style_scanned', marks=ocr), pytest.param('billing_app_logo_image', marks=ocr)],
    'borderless_style': ['borderless_style', pytest.param('borderless_style_scanned', marks=ocr), pytest.param('borderless_image_blocks', marks=ocr)],
}
CASES = [(base, v) for base, variants in VARIANTS.items() for v in variants]


@pytest.fixture(scope='session')
def samples(tmp_path_factory):
    return make_all(tmp_path_factory.mktemp('samples'))


def run(samples, name):
    return build_response(extract_pdf((samples / f'{name}.pdf').read_bytes()), f'{name}.pdf')['data']


@pytest.mark.parametrize('base,variant', [pytest.param(b, v, id=(v.values[0] if hasattr(v, 'values') else v), marks=getattr(v, 'marks', ())) for b, v in CASES])
def test_invoice_fields(samples, base, variant):
    name = variant.values[0] if hasattr(variant, 'values') else variant
    data = run(samples, name)
    fields = data['extracted_fields']
    lines = next(t for t in data['tables'] if t['table_name'] == 'line_items')['rows']
    got = dict(invoice_number=fields['invoice_details']['invoice_number'], invoice_date=fields['invoice_details']['invoice_date'],
               invoice_total=fields['totals']['invoice_total'], taxable_amount=fields['totals']['taxable_amount'],
               gst_amount=fields['totals']['total_tax'], item_count=len(lines))
    assert got == EXPECTED[base], (name, got)
    assert data['document_metadata']['document_category'] == 'tax_invoice'
    assert data['validation']['status'] == 'passed', data['validation']
    assert all(i['description'] and (i['taxable_amount'] is not None or i['total_amount'] is not None) for i in lines)
    assert fields['buyer']['name'] == 'Northwind Finserv Pvt Ltd' and fields['buyer']['gstin'] == '27AAACN9876K1ZQ'
    assert fields['supplier']['gstin'] and fields['supplier']['gstin'] != fields['buyer']['gstin']
    assert fields['bank_details'].get('account_number')


@ocr
def test_letterhead_image_fields_are_recovered(samples):
    """Supplier GSTIN, phone and email exist only inside an image; a text-layer-only read would miss them."""
    data = run(samples, 'billing_app_logo_image')
    assert data['processing']['page_sources'][0]['source'] == 'digital+ocr'
    supplier = data['extracted_fields']['supplier']
    assert (supplier['gstin'], supplier['phone'], supplier['email']) == ('27AAECZ4321L1Z3', '9820012345', 'billing@zenithvending.example')


@ocr
def test_image_blocks_hold_invoice_number_and_totals(samples):
    data = run(samples, 'borderless_image_blocks')
    assert data['extracted_fields']['invoice_details']['po_number'] == 'NF/PO/3391'
    assert data['extracted_fields']['invoice_details']['due_date'] == '14 Apr 2026'


def test_nothing_is_dropped(samples):
    """Every text segment is in a field, a table, or unmapped_text."""
    for name in EXPECTED:
        data = run(samples, name)
        cov = data['coverage']
        assert cov['mapped_to_fields_or_tables'] + cov['returned_as_unmapped_text'] == cov['text_segments']
        assert len(data['unmapped_text']) == cov['returned_as_unmapped_text']


def test_repeated_labels_go_to_the_right_party(samples):
    """GSTIN / State appear once for the supplier and once for the buyer; each must land in its own section."""
    fields = run(samples, 'billing_app_style')['extracted_fields']
    assert fields['supplier']['gstin'] == '27AAECZ4321L1Z3' and fields['buyer']['gstin'] == '27AAACN9876K1ZQ'
    assert fields['supplier']['name'].startswith('Zenith Vending') and fields['supplier']['address'].startswith('Unit 12')
    assert fields['totals']['amount_in_words'].startswith('Two Thousand Nine Hundred')
    assert fields['bank_details']['ifsc_code'] == 'EXMB0004321'


def test_no_false_pass_without_independent_evidence():
    from app.financial import validate
    derived = {'invoice_total': 118.0, 'gst_amount': 18.0, 'taxable_amount': 100.0, 'taxable_amount_source': 'derived_from_total_and_tax'}
    assert validate(derived, [])['status'] == 'not_verified'


def test_amount_in_words():
    from app.financial import words_to_number
    assert words_to_number('INR One Thousand Six Hundred Fifty Two Only') == 1652.0
    assert words_to_number('Rupees Two Lakh Fifty Thousand and Fifty Paise Only') == 250000.5
    assert words_to_number('Two Thousand Nine Hundred and Fifty Rupees only') == 2950.0


def test_non_invoice_returns_generic_fields(samples):
    data = run(samples, 'letter')
    assert data['document_metadata']['document_category'] == 'sanction_letter'
    assert data['extracted_fields']['loan_amount'] == 'INR 2,50,00,000'
    assert any('drawdown' in u['text'] for u in data['text_content'])   # the paragraph comes back joined, in reading order


def test_tax_label_needs_its_own_amount():
    """A 'CGST  SGST' heading row next to an unrelated amount (e.g. Round Off) must not become the tax value."""
    from app.financial import extract_totals
    def row(y, *cells):
        return [(x, y, x + 30, y + 8, text, n + y, 0, 0) for n, (x, text) in enumerate(cells)]
    words = row(10, (10, 'CGST'), (60, 'SGST'), (300, 'Round'), (335, 'Off'), (380, ':'), (400, '-'), (410, '0.35'))
    totals = extract_totals(words)
    assert 'cgst' not in totals and 'sgst' not in totals
    assert extract_totals(row(10, (10, 'CGST'), (60, '126.00')))['cgst'] == 126.0
    assert extract_totals(row(10, (10, 'CGST'), (60, '9'), (70, '%'), (100, '126.00')))['cgst'] == 126.0


def test_table_without_grid_and_two_line_headings():
    """Any report: column titles on two lines, one title cell spanning two columns, no ruled lines."""
    from app.generic_tables import detect_tables
    def row(y, *cells):
        words = []
        for x, text in cells:
            for part in text.split(' '):
                words.append((x, y, x + 5 * len(part), y + 8, part, y, 0, 0)); x += 5 * len(part) + 3
        return words
    words = (row(10, (18, 'Sr. Trade Ref'), (150, 'Order'), (250, 'Amount'))
             + row(20, (18, 'No.'), (150, 'Type'), (250, '(Cr.)'))
             + row(40, (20, '1'), (40, 'TREP/150626'), (150, 'Borrow'), (250, '100.00'))
             + row(52, (20, '2'), (40, 'TREP/150626'), (150, 'Borrow'), (255, '40.00')))
    table = detect_tables(words, 1)[0]
    assert table['columns'] == ['Sr. No.', 'Trade Ref', 'Order Type', 'Amount (Cr.)']
    assert table['rows'][1] == {'sr_no': '2', 'trade_ref': 'TREP/150626', 'order_type': 'Borrow', 'amount_cr': '40.00'}


def _row(y, *cells):
    words = []
    for x, text in cells:
        for part in text.split(' '):
            words.append((x, y, x + 5 * len(part), y + 8, part, y, 0, 0)); x += 5 * len(part) + 3
    return words


def test_label_value_table_without_colons():
    """Terms table of a letter: centred labels, no colons, one value over two lines with its label beside the middle."""
    from app.generic_fields import key_values_from_words
    words = (_row(100, (60, 'Drawdown Date'), (200, '24-06-2026'))
             + _row(125, (50, 'Rate of Interest'), (200, '8.20% (Linked to 1M MCLR)'))
             + _row(150, (70, 'Purpose'), (200, 'Working Capital Purposes'))
             + _row(170, (200, 'Minimum tenor shall be 7 days; option to prepay'))
             + _row(177, (45, 'Prepayment Penalty'))
             + _row(184, (200, 'without penalty after 7 days.')))
    got = {kv['label']: kv['value'] for kv in key_values_from_words(words, 1)}
    assert got['Drawdown Date'] == '24-06-2026' and got['Rate of Interest'].startswith('8.20%')
    assert got['Prepayment Penalty'] == 'Minimum tenor shall be 7 days; option to prepay without penalty after 7 days.'


def test_tilted_scan_is_straightened():
    """On a scan tilted by about one degree a label and its value sit several points apart vertically."""
    from app.extractor import straighten
    from app.generic_fields import _rows_from_words
    tilted = []
    for n in range(8):
        y = 100 + n * 20
        tilted += [(40, y, 100, y + 9, f'Label{n}', n, 0, 0), (400, y - 7, 460, y + 2, f'value{n}', n, 0, 1)]
    assert len(_rows_from_words(tilted)) == 16            # before: every label and value on its own row
    assert len(_rows_from_words(straighten(tilted))) == 8  # after: one row per printed line


def test_letter_parts():
    from app.letters import letter_parts
    words = (_row(100, (30, '24 June, 2026')) + _row(112, (30, 'To')) + _row(124, (30, 'Example Bank')) + _row(136, (30, 'Chennai'))
             + _row(170, (30, 'Subject - Drawdown of Working Capital Demand Loan')))
    parts, _ = letter_parts(words, 800)
    assert parts == {'date': '2026-06-24', 'addressed_to': ['Example Bank', 'Chennai'], 'subject': 'Drawdown of Working Capital Demand Loan'}