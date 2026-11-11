"""Conservative document classification; no LLM or vendor-specific templates."""
import re


def classify(text):
    sample = text[:15000]
    if re.search(r"\b(?:tax invoice|invoice no\.?|invoice number|invoice date|invoice\s*#|bill of supply)", sample, re.I):
        return "tax_invoice"
    if re.search(r"\b(?:credit arrangement letter|CAL)\b", sample, re.I) and re.search(r"\b(?:facility|drawdown|borrower)\b", sample, re.I):
        return "credit_arrangement_letter"
    if re.search(r"\b(?:sanction letter|sanctioned limit)\b", sample, re.I):
        return "sanction_letter"
    if re.search(r"\b(?:all\s+)?trades?\s+report\b|\bdeal\s+report\b", sample, re.I):
        return "trade_report"
    if re.search(r"\btrade confirmation\b", sample, re.I):
        return "trade_confirmation"
    return "unknown"