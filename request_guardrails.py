"""Recognise explicit numeric year mentions before executing financial tools.

This is a deliberately limited check, not a complete natural-language parser.
It supports four-digit fiscal years, lists and ascending inclusive ranges.
Dates, URLs and obvious monetary amounts do not supply a fiscal-year label.
Relative years and words such as 'twenty twenty-five' require clarification.
Company/metric intent and completeness still need separate evaluation.
New alternative measures also require explicit wording before a tool is run.
"""

import re
import unicodedata


_YEAR = r"(?:19\d{2}|20\d{2}|2100)"
_LABEL = rf"(?:FY\s*|fiscal\s+years?\s*)?{_YEAR}"
_YEAR_MENTION = re.compile(rf"(?<!\w){_LABEL}(?!\w)", re.IGNORECASE)
_RANGE_JOIN = re.compile(r"\s*(?:-|–|—|to|through)\s*", re.IGNORECASE)
_SHORT_RANGE = re.compile(
    rf"(?<!\w){_LABEL}\s*(?:-|–|—|/|to|through)\s*(?:FY\s*)?\d{{1,3}}(?!\d)",
    re.IGNORECASE,
)
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_NON_YEAR_CONTEXTS = [
    # Mask complete URLs, then common dates before looking for year labels.
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"(?<!\w)\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?(?!\d)"),
    re.compile(r"(?<!\d)\d{1,2}[-/.]\d{1,2}[-/.]\d{4}(?!\d)"),
    re.compile(rf"\b{_MONTH}\s+(?:\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+)?{_YEAR}\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\s+{_YEAR}\b", re.IGNORECASE),
    re.compile(r"(?:[$€£]|\b(?:USD|EUR|GBP)\s*)\s*\d[\d,.]*", re.IGNORECASE),
    re.compile(r"\b\d[\d,.]*\s*(?:dollars|million|billion|trillion|USD|EUR|GBP)\b", re.IGNORECASE),
    re.compile(r"\b\d[\d,]*\.\d+\b"),
]


def requested_fiscal_years(question: str) -> frozenset[int]:
    """Extract supported year mentions without inferring a default or 'latest'.

    A date such as 2025-12-31 is not an FY2025 request. Range endpoints must
    both have four digits; ambiguous shorthand is left for clarification.
    Presence is only a necessary condition: mentioning a year does not prove
    that every requested company/metric should use it.
    """
    text = unicodedata.normalize("NFKC", question)
    for pattern in _NON_YEAR_CONTEXTS:
        text = pattern.sub(lambda match: " " * len(match.group()), text)
    if _SHORT_RANGE.search(text):
        return frozenset()
    mentions = list(_YEAR_MENTION.finditer(text))
    years = {int(match.group()[-4:]) for match in mentions}
    for left, right in zip(mentions, mentions[1:]):
        if _RANGE_JOIN.fullmatch(text[left.end():right.start()]):
            start, end = int(left.group()[-4:]), int(right.group()[-4:])
            if start <= end:
                years.update(range(start, end + 1))
            else:
                return frozenset()
    return frozenset(years)


def alternative_metric_is_explicit(question: str, metric: str) -> bool:
    """Prevent automatic swaps to the newly supported, differently defined measures.

    Deliberately conservative: unsupported wording needs a clearer question.
    This is a whole-question check, not a complete company-to-measure parser.
    """
    text = unicodedata.normalize("NFKC", question).casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text)
    if re.search(r"\b(?:non[\s‐‑–—-]*gaap|adjusted)\b", text):
        return False  # A GAAP result cannot answer an adjusted-earnings request.
    if metric not in {"net_income_consolidated", "pretax_income"}:
        return True
    if metric == "net_income_consolidated":
        return bool(re.search(
            r"\bconsolidated net (?:income|profit|loss)\b|"
            r"\bnet (?:income|profit|loss) (?:including|inclusive of) "
            r"(?:the (?:portion|share) attributable to )?non[ -]?controlling interests?\b", text))
    return bool(re.search(r"\bpre[\s‐‑–—-]*tax (?:income|profit|loss)\b|"
                          r"\b(?:income|profit|loss)(?: from continuing operations)? before (?:income )?tax(?:es)?\b", text))
