"""
core/language.py — Lightweight language detection for Persian vs English.
"""


def detect_language(text: str) -> str:
    """
    Returns 'fa' for Persian/Farsi, 'en' otherwise.
    Uses Unicode range check — no external dependency needed.
    """
    fa_count = sum(1 for ch in text if "\u0600" <= ch <= "\u06FF")
    return "fa" if fa_count > len(text) * 0.15 else "en"
