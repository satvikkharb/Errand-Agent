"""Make the facts a customer scans for stand out: the service, the provider, the date and the time.

The model is asked to bold them, but free text from a model is not a reliable place for formatting, so
this runs on the finished reply instead. It only wraps words it can prove are facts (provider names
the user has dealt with, service names, and date and time expressions) in ** (the app renders that as
bold). A reply the model already formatted is left alone, and the wording is never changed.
"""
import re

_DAY = r"(?:Mon(?:day)?|Tue(?:s|sday)?|Wed(?:nesday)?|Thu(?:rs|rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)"
_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_CLOCK = r"\d{1,2}(?::\d{2})?"
_SERVICES = ("ac servicing", "ac service", "plumber", "electrician")

_DATE = rf"(?:{_DAY},?\s+)?\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b"  # "Mon 2 Nov", "Monday, 2nd November"
_DATE2 = rf"(?:{_DAY},?\s+)?{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b"  # month first: "Wed, Nov 4", "Wednesday, November 4th"
_RANGE = rf"{_CLOCK}\s*(?:am|pm)?\s*(?:to|-|–|—)\s*{_CLOCK}\s*(?:am|pm)\b"  # "2-4 pm", "6 to 8 pm", "11 am-1 pm"
_TIME = rf"\b{_CLOCK}\s*(?:am|pm)\b"  # "11 am"


def emphasize(text: str, provider_names: list[str] | set[str] = ()) -> str:
    if not text or "**" in text:
        return text  # empty, or already formatted by the model
    names = sorted({n.strip() for n in provider_names if n and n.strip()}, key=len, reverse=True)
    parts = []
    if names:
        parts.append(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")
    parts += [rf"\b{_DATE}", rf"\b{_DATE2}", _RANGE, _TIME, r"\b(?:" + "|".join(re.escape(s) for s in _SERVICES) + r")\b"]
    return re.sub("|".join(f"(?:{p})" for p in parts), lambda m: f"**{m.group(0)}**", text, flags=re.IGNORECASE)
