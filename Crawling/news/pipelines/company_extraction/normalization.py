"""Unicode normalization with half-open offsets into the original Python string."""

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class NormalizedText:
    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("invalid normalized span")
        return self.starts[start], self.ends[end - 1]


def normalize(text: str) -> NormalizedText:
    chars, starts, ends = [], [], []
    i = 0
    while i < len(text):
        start = i
        i += 1
        # NFC composition of decomposed Hangul must happen before case folding.
        if 0x1100 <= ord(text[start]) <= 0x1112 and i < len(text) and 0x1161 <= ord(text[i]) <= 0x1175:
            i += 1
            if i < len(text) and 0x11A8 <= ord(text[i]) <= 0x11C2:
                i += 1
        while i < len(text) and unicodedata.combining(text[i]):
            i += 1
        for char in unicodedata.normalize("NFKC", text[start:i]).casefold():
            if unicodedata.category(char) == "Cf":
                continue
            if char.isspace():
                char = " "
                if chars and chars[-1] == " ":
                    ends[-1] = i
                    continue
            elif char in "‐‑‒–—−":
                char = "-"
            elif char in "‘’":
                char = "'"
            chars.append(char)
            starts.append(start)
            ends.append(i)
    return NormalizedText("".join(chars), tuple(starts), tuple(ends))


def key(text: str) -> str:
    return normalize(text).text.strip()
