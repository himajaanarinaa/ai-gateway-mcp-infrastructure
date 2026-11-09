import re

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"(?=.{1,254}(?![A-Za-z0-9.-]))"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![A-Za-z0-9.-])"
)
SSN_RE = re.compile(r"(?<!\d)(?:\d{3}-\d{2}-\d{4}|\d{9})(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
EMAIL_SUFFIX_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~@-]{1,254}$")
CARD_SUFFIX_RE = re.compile(r"\d[\d -]{0,39}$")


def redact(text: str) -> str:
    text = EMAIL_RE.sub("[REDACTED]", text)
    text = SSN_RE.sub("[REDACTED]", text)
    return CARD_RE.sub("[REDACTED]", text)


class IncrementalRedactor:
    def __init__(self) -> None:
        self.pending = ""

    def feed(self, text: str, final: bool = False) -> str:
        self.pending += text
        if final:
            safe, self.pending = self.pending, ""
            return redact(safe)

        cut = len(self.pending)
        for pattern in (EMAIL_SUFFIX_RE, CARD_SUFFIX_RE):
            match = pattern.search(self.pending)
            if match:
                cut = min(cut, match.start())

        for pattern in (EMAIL_RE, SSN_RE, CARD_RE):
            for match in pattern.finditer(self.pending):
                if match.start() < cut < match.end():
                    cut = match.start()

        safe, self.pending = self.pending[:cut], self.pending[cut:]
        return redact(safe)
