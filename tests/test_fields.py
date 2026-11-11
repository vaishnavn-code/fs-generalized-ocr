import unittest
from app.extractor import fields_from_text

class TestFields(unittest.TestCase):
    def test_amount_rate_date(self):
        fields = fields_from_text('Loan Amount: INR 2,500,000\nInterest Rate: 8.5%\nDate: 10/11/2026')
        self.assertEqual(fields['loan_amount'], '2,500,000')
        self.assertEqual(fields['interest_rate_percent'], '8.5')
        self.assertEqual(fields['date'], '10/11/2026')

if __name__ == '__main__':
    unittest.main()
