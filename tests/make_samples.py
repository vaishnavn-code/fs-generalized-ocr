"""Generate synthetic digital invoices (fictional parties) in three different layouts.
Usage: python -m tests.make_samples [out_dir]   -> tally_style.pdf, billing_app_style.pdf, borderless_style.pdf, letter.pdf"""
import sys
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

W, H = A4
F, FB = 'Helvetica', 'Helvetica-Bold'
RUPEE = 'Rs.'
for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'C:/Windows/Fonts/Nirmala.ttf'):
    if Path(path).exists():
        pdfmetrics.registerFont(TTFont('Body', path)); F = 'Body'; RUPEE = '\u20b9'
        bold = path.replace('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')
        if Path(bold).exists() and bold != path:
            pdfmetrics.registerFont(TTFont('BodyB', bold)); FB = 'BodyB'
        else:
            FB = 'Body'
        break


def t(c, x, y, s, size=8, bold=False, right=False, center=False):
    c.setFont(FB if bold else F, size)
    (c.drawRightString if right else c.drawCentredString if center else c.drawString)(x, H - y, s)


def box(c, x, y, w, h):
    c.rect(x, H - y - h, w, h)


def hl(c, x0, x1, y):
    c.line(x0, H - y, x1, H - y)


def vl(c, x, y0, y1):
    c.line(x, H - y0, x, H - y1)


def tally_style(path):
    """Fully gridded layout: labels above values, tax rows inside the item grid, tax summary below."""
    c = canvas.Canvas(str(path), pagesize=A4); c.setLineWidth(0.5)
    L, R = 30, 565
    t(c, W / 2, 30, 'Tax Invoice', 11, True, center=True)
    box(c, L, 40, R - L, 150); vl(c, 300, 40, 190); hl(c, L, 300, 115)
    for y in (65, 90, 115): hl(c, 300, R, y)
    vl(c, 432, 40, 115)
    t(c, 34, 52, 'ACME INFOTECH', 9, True)
    for i, s in enumerate(['Shop 4, Lotus Arcade, Station Road', 'Andheri East, Mumbai - 400069', 'GSTIN/UIN: 27ABCDE1234F1Z5',
                           'State Name : Maharashtra, Code : 27', 'E-Mail : accounts@acmeinfotech.example']):
        t(c, 34, 63 + i * 10, s)
    t(c, 34, 126, 'Buyer (Bill to)')
    t(c, 34, 137, 'Northwind Finserv Pvt Ltd', 9, True)
    for i, s in enumerate(['7th Floor, Orbit Tower, MIDC', 'Mumbai - 400093', 'GSTIN/UIN : 27AAACN9876K1ZQ', 'State Name : Maharashtra, Code : 27']):
        t(c, 34, 148 + i * 10, s)
    grid = [(304, 49, 'Invoice No.', 'GST/263/25-26'), (436, 49, 'Dated', '1-Sep-25'),
            (304, 74, 'Delivery Note', ''), (436, 74, 'Mode/Terms of Payment', '15 Days'),
            (304, 99, "Buyer's Order No.", 'PO-7781'), (436, 99, 'Dated', '25-Aug-25')]
    for x, y, label, value in grid:
        t(c, x, y, label, 7); t(c, x, y + 11, value, 8, True)
    t(c, 304, 124, 'Terms of Delivery', 7)
    cols = [L, 55, 290, 345, 400, 450, 480, R]
    top, bottom = 190, 400
    box(c, L, top, R - L, bottom - top)
    for x in cols[1:-1]: vl(c, x, top, bottom)
    hl(c, L, R, top + 22); hl(c, L, R, bottom - 16)
    for x, s in zip([33, 120, 294, 349, 410, 455, 510], ['Sl No.', 'Description of Goods', 'HSN/SAC', 'Quantity', 'Rate', 'per', 'Amount']):
        t(c, x, top + 13, s, 8)
    t(c, 38, 225, '1'); t(c, 59, 225, 'Ceiling Fan 1200mm', 8, True); t(c, 59, 235, 'Model: Breeze Plus, white', 7)
    t(c, 294, 225, '84145120'); t(c, 395, 225, '1 Nos', right=True); t(c, 445, 225, '900.00', right=True); t(c, 455, 225, 'Nos'); t(c, 560, 225, '900.00', right=True)
    t(c, 38, 250, '2'); t(c, 59, 250, 'Fan Regulator', 8, True)
    t(c, 294, 250, '85365090'); t(c, 395, 250, '2 Nos', right=True); t(c, 445, 250, '250.00', right=True); t(c, 455, 250, 'Nos'); t(c, 560, 250, '500.00', right=True)
    hl(c, 480, R, 268); t(c, 560, 280, '1,400.00', right=True)
    t(c, 285, 295, 'CGST', 8, True, right=True); t(c, 445, 295, '9', right=True); t(c, 455, 295, '%'); t(c, 560, 295, '126.00', right=True)
    t(c, 285, 307, 'SGST', 8, True, right=True); t(c, 445, 307, '9', right=True); t(c, 455, 307, '%'); t(c, 560, 307, '126.00', right=True)
    t(c, 285, bottom - 5, 'Total', right=True); t(c, 395, bottom - 5, '3 Nos', right=True); t(c, 560, bottom - 5, RUPEE + ' 1,652.00', 8, True, right=True)
    t(c, 34, 412, 'Amount Chargeable (in words)'); t(c, 560, 412, 'E. & O.E', 7, right=True)
    t(c, 34, 424, 'INR One Thousand Six Hundred Fifty Two Only', 8, True)
    tcols = [L, 200, 280, 320, 380, 420, 480, R]; ttop = 432
    box(c, L, ttop, R - L, 62)
    for x in tcols[1:-1]: vl(c, x, ttop, ttop + 62)
    hl(c, L, R, ttop + 24); hl(c, L, R, ttop + 36); hl(c, L, R, ttop + 48)
    for x, s in zip([34, 204, 284, 324, 384, 424, 484], ['HSN/SAC', 'Taxable Value', 'CGST Rate', 'CGST Amount', 'SGST Rate', 'SGST Amount', 'Total Tax Amount']):
        t(c, x, ttop + 14, s, 7)
    for y, row in zip([ttop + 33, ttop + 45, ttop + 58], [['84145120', '900.00', '9%', '81.00', '9%', '81.00', '162.00'],
                                                      ['85365090', '500.00', '9%', '45.00', '9%', '45.00', '90.00'],
                                                      ['Total', '1,400.00', '', '126.00', '', '126.00', '252.00']]):
        t(c, 34, y, row[0], 8, row[0] == 'Total')
        for x, s in zip([276, 316, 376, 416, 476, 560], row[1:]): t(c, x, y, s, right=True)
    t(c, 34, 508, 'Tax Amount (in words) : INR Two Hundred Fifty Two Only')
    t(c, 300, 525, "Company's Bank Details")
    t(c, 300, 536, 'Bank Name : Example Bank Ltd'); t(c, 300, 547, 'A/c No. : 000111222333'); t(c, 300, 558, 'Branch & IFS Code : Andheri East & EXMB0001234')
    t(c, 34, 525, "Company's PAN : ABCDE1234F")
    t(c, 34, 545, 'Declaration', 7)
    t(c, 34, 556, 'We declare that this invoice shows the actual price of the goods', 7); t(c, 34, 565, 'described and that all particulars are true and correct.', 7)
    t(c, 560, 580, 'for ACME INFOTECH', 8, True, right=True); t(c, 560, 610, 'Authorised Signatory', 7, right=True)
    t(c, W / 2, 630, 'This is a Computer Generated Invoice', 7, center=True)
    c.save()


def billing_app_style(path):
    """Colon labels, gridded items with a GST column and a gross Amount column, totals block at the right."""
    c = canvas.Canvas(str(path), pagesize=A4); c.setLineWidth(0.5)
    L, R = 30, 565
    t(c, L, 40, 'Zenith Vending Solutions Pvt. Ltd.', 13, True)
    t(c, L, 54, 'Unit 12, Sunrise Industrial Estate, Vasai East, Palghar - 401208')
    t(c, L, 65, 'Phone: 9820012345'); t(c, 200, 65, 'Email: billing@zenithvending.example')
    t(c, L, 76, 'GSTIN: 27AAECZ4321L1Z3'); t(c, 200, 76, 'State: 27-Maharashtra')
    hl(c, L, R, 84); t(c, W / 2, 98, 'Tax Invoice', 11, True, center=True)
    t(c, L, 115, 'Bill To', 8, True); t(c, L, 127, 'Northwind Finserv Pvt Ltd', 9, True)
    t(c, L, 138, '7th Floor, Orbit Tower, MIDC, Andheri East, Mumbai'); t(c, L, 149, 'Contact No.: 02240001111'); t(c, L, 160, 'GSTIN: 27AAACN9876K1ZQ')
    t(c, R, 115, 'Invoice Details', 8, True, right=True); t(c, R, 127, 'Invoice No.: ZVS/25-26/1843', right=True)
    t(c, R, 138, 'Date: 02-02-2026', right=True); t(c, R, 149, 'Place of supply: 27-Maharashtra', right=True)
    cols = [L, 50, 240, 300, 360, 430, 500, R]; top = 172
    box(c, L, top, R - L, 60)
    for x in cols[1:-1]: vl(c, x, top, top + 60)
    hl(c, L, R, top + 16); hl(c, L, R, top + 44)
    for x, s in zip([36, 54, 244, 304, 364, 434, 504], ['#', 'Item name', 'HSN/SAC', 'Quantity', 'Price/Unit', 'GST', 'Amount']):
        t(c, x, top + 11, s, 8, True)
    t(c, 36, top + 28, '1'); t(c, 54, top + 28, 'Vending Machine Rentals'); t(c, 54, top + 38, 'February 2026', 7)
    t(c, 244, top + 28, '997319'); t(c, 355, top + 28, '1', right=True)
    t(c, 425, top + 28, RUPEE + ' 2,500.00', right=True); t(c, 495, top + 28, RUPEE + ' 450.00 (18%)', right=True); t(c, 560, top + 28, RUPEE + ' 2,950.00', right=True)
    t(c, 54, top + 55, 'Total', 8, True); t(c, 355, top + 55, '1', right=True); t(c, 495, top + 55, RUPEE + ' 450.00', right=True); t(c, 560, top + 55, RUPEE + ' 2,950.00', right=True)
    t(c, L, 250, 'Invoice Amount In Words', 8, True); t(c, L, 262, 'Two Thousand Nine Hundred and Fifty Rupees only')
    for y, a, b in [(250, 'Sub Total', RUPEE + ' 2,500.00'), (262, 'SGST@9%', RUPEE + ' 225.00'), (274, 'CGST@9%', RUPEE + ' 225.00'),
                    (286, 'Total', RUPEE + ' 2,950.00'), (298, 'Received', RUPEE + ' 0.00'), (310, 'Balance', RUPEE + ' 2,950.00')]:
        t(c, 400, y, a, 8, a == 'Total'); t(c, 470, y, ':'); t(c, R, y, b, 8, a == 'Total', right=True)
    t(c, L, 330, 'Tax Summary:', 8, True)
    tcols = [L, 130, 250, 310, 390, 450, 520, R]; ttop = 336
    box(c, L, ttop, R - L, 44)
    for x in tcols[1:-1]: vl(c, x, ttop, ttop + 44)
    hl(c, L, R, ttop + 16); hl(c, L, R, ttop + 30)
    for x, s in zip([34, 134, 254, 314, 394, 454, 524], ['HSN/SAC', 'Taxable amount', 'CGST Rate', 'CGST Amount', 'SGST Rate', 'SGST Amount', 'Total Tax']):
        t(c, x, ttop + 11, s, 7, True)
    for y, row in zip([ttop + 26, ttop + 40], [['997319', '2,500.00', '9%', '225.00', '9%', '225.00', '450.00'], ['TOTAL', '2,500.00', '', '225.00', '', '225.00', '450.00']]):
        t(c, 34, y, row[0])
        for x, s in zip([246, 306, 386, 446, 516, 560], row[1:]): t(c, x, y, s, right=True)
    t(c, L, 400, 'Terms and conditions', 8, True); t(c, L, 412, 'Thanks for doing business with us! Payment is due within 15 days.')
    t(c, L, 432, 'Bank Details', 8, True); t(c, L, 444, 'Name : Example Bank Ltd, Vasai Branch'); t(c, L, 455, 'Account No. : 009988776655')
    t(c, L, 466, 'IFSC code : EXMB0004321'); t(c, L, 477, "Account Holder's Name : Zenith Vending Solutions Pvt. Ltd.")
    t(c, R, 432, 'For: Zenith Vending Solutions Pvt. Ltd.', right=True); t(c, R, 477, 'Authorized Signatory', right=True)
    c.save()


def borderless_style(path):
    """No grid at all, different header words (Particulars / Unit Price / Taxable Value / IGST / Total)."""
    c = canvas.Canvas(str(path), pagesize=A4); c.setLineWidth(0.5)
    L, R = 40, 555
    t(c, L, 50, 'Kestrel Analytics LLP', 14, True); t(c, R, 50, 'INVOICE', 14, True, right=True)
    t(c, L, 64, '221 Harbour Road, Kochi, Kerala 682001'); t(c, L, 75, 'GSTIN: 32AAKFK5566M1Z8'); t(c, L, 86, 'PAN: AAKFK5566M')
    t(c, 380, 75, 'Invoice #:'); t(c, R, 75, 'INV-2026-0042', right=True)
    t(c, 380, 86, 'Invoice Date:'); t(c, R, 86, '15 Mar 2026', right=True)
    t(c, 380, 97, 'Due Date:'); t(c, R, 97, '14 Apr 2026', right=True)
    t(c, 380, 108, 'PO Number:'); t(c, R, 108, 'NF/PO/3391', right=True)
    t(c, L, 125, 'Billed To', 8, True); t(c, L, 137, 'Northwind Finserv Pvt Ltd'); t(c, L, 148, '7th Floor, Orbit Tower, MIDC, Mumbai 400093')
    t(c, L, 159, 'GSTIN: 27AAACN9876K1ZQ'); t(c, L, 170, 'Place of Supply: Maharashtra (27)')
    y = 200
    for x, s, r in [(L, 'Particulars', False), (215, 'SAC', False), (290, 'Qty', True), (355, 'Unit Price', True), (430, 'Taxable Value', True), (490, 'IGST', True), (R, 'Total', True)]:
        t(c, x, y, s, 8, True, right=r)
    hl(c, L, R, y + 5)
    rows = [('Data pipeline consulting', '998313', '10', '800.00', '8,000.00', '1,440.00', '9,440.00'),
            ('Dashboard maintenance retainer', '998314', '1', '2,000.00', '2,000.00', '360.00', '2,360.00')]
    for i, (d, sac, q, up, tv, ig, tot) in enumerate(rows):
        yy = y + 20 + i * 26
        t(c, L, yy, d); t(c, 215, yy, sac); t(c, 290, yy, q, right=True); t(c, 355, yy, up, right=True); t(c, 430, yy, tv, right=True); t(c, 490, yy, ig, right=True); t(c, R, yy, tot, right=True)
        t(c, L, yy + 10, 'March 2026 engagement' if i == 0 else 'Monthly', 7)
    hl(c, L, R, 262)
    for yy, a, b in [(278, 'Taxable Value', '10,000.00'), (290, 'IGST @ 18%', '1,800.00'), (302, 'Grand Total', '11,800.00')]:
        t(c, 400, yy, a, 8, a == 'Grand Total'); t(c, R, yy, b, 8, a == 'Grand Total', right=True)
    t(c, L, 325, 'Amount in words: Rupees Eleven Thousand Eight Hundred Only')
    t(c, L, 350, 'Payment Information', 8, True); t(c, L, 362, 'Bank: Example Bank Ltd'); t(c, L, 373, 'Account Number: 123400005678'); t(c, L, 384, 'IFSC: EXMB0009876')
    t(c, L, 410, 'Late payments attract interest at 1.5% per month.'); t(c, L, 440, 'Authorised Signatory', 8)
    c.save()


def letter(path):
    """Non-invoice document: generic fields only."""
    c = canvas.Canvas(str(path), pagesize=A4)
    t(c, 40, 50, 'Example Bank Ltd', 13, True); t(c, 40, 70, 'Ref No: EBL/SL/2026/0193'); t(c, 400, 70, 'Date: 10/11/2026')
    t(c, 40, 95, 'Sanction Letter', 11, True)
    t(c, 40, 115, 'Borrower: Northwind Finserv Pvt Ltd'); t(c, 40, 127, 'Loan Amount: INR 2,50,00,000'); t(c, 40, 139, 'Interest Rate: 9.25% per annum')
    t(c, 40, 151, 'Tenor: 36 months'); t(c, 40, 175, 'The facility is subject to the terms set out in the master agreement and')
    t(c, 40, 186, 'is available for drawdown within ninety days from the date of this letter.')
    c.save()


def scanned_copy(src, dst, dpi=200):
    """Fully scanned version of a PDF: every page becomes one image, no text layer."""
    import pymupdf
    with pymupdf.open(src) as doc, pymupdf.open() as out:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=pix)
        out.save(dst)


def hybrid_copy(src, dst, bands, dpi=200):
    """Digital PDF where some horizontal bands (y0, y1 in points from the top) are replaced by images of
    themselves - like a letterhead/logo image, a pasted scanned table or a stamped totals block."""
    import pymupdf
    with pymupdf.open(src) as doc:
        page = doc[0]
        shots = []
        for y0, y1 in bands:
            rect = pymupdf.Rect(20, y0, page.rect.width - 20, y1)
            shots.append((rect, page.get_pixmap(dpi=dpi, clip=rect)))
        for rect, _ in shots:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        for rect, pix in shots:
            page.insert_image(rect, pixmap=pix)
        doc.save(dst)


def make_all(out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    for name, fn in [('tally_style', tally_style), ('billing_app_style', billing_app_style), ('borderless_style', borderless_style), ('letter', letter)]:
        fn(out / f'{name}.pdf')
    for name in ('tally_style', 'billing_app_style', 'borderless_style'):
        scanned_copy(out / f'{name}.pdf', out / f'{name}_scanned.pdf')
    # Letterhead (supplier, GSTIN, phone/email) as an image; rest digital.
    hybrid_copy(out / 'billing_app_style.pdf', out / 'billing_app_logo_image.pdf', [(25, 82)])
    # Invoice number/date/PO block and totals block as images; rest digital.
    hybrid_copy(out / 'borderless_style.pdf', out / 'borderless_image_blocks.pdf', [(66, 112), (268, 330)])
    # Item grid and tax summary pasted as scans; header digital.
    hybrid_copy(out / 'tally_style.pdf', out / 'tally_scanned_tables.pdf', [(188, 498)])
    return out


if __name__ == '__main__':
    print(make_all(sys.argv[1] if len(sys.argv) > 1 else 'tests/samples'))