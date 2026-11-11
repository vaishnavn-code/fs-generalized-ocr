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
    assert any('drawdown' in u['text'] for u in data['unmapped_text'])


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
