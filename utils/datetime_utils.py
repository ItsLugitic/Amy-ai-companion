"""
utils/datetime_utils.py — Human-readable date/time strings for memory context.
"""
import datetime
from num2words import num2words


def get_datetime_in_words() -> str:
    now = datetime.datetime.now()
    month_name  = now.strftime("%B")
    day_word    = num2words(now.day, ordinal=True)
    year_word   = num2words(now.year)
    hour_12     = now.hour % 12 or 12
    hour_word   = num2words(hour_12)
    minute_word = num2words(now.minute).replace("-", " ")
    am_pm       = "am" if now.hour < 12 else "pm"
    return f"ON {month_name} {day_word}, {year_word}, at {hour_word} {minute_word} {am_pm}"
