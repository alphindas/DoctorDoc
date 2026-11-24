"""Score how much a piece of text looks like real human text (0 = junk, 1 = clean).

Used twice: triage uses it to spot garbled PDF text layers, and the router uses it to
reject bad engine output and fall back to the next engine.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_CID = re.compile(r"\(cid:\d+\)")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)  # runs of letters
_TOKEN = re.compile(r"\S+")
_VOWELS = set("aeiouyAEIOUYáàâäãåæéèêëíìîïóòôöõøúùûüýÿœ")
# Characters that may appear inside a normal token without being "odd".
_TOKEN_PUNCT = set(".,-_/'’%:;&@#+()[]\"°$€£")


@dataclass
class QualityReport:
    """The score plus the reasons, so we can explain a rejection."""

    score: float
    chars: int
    penalties: dict[str, float] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        if not self.penalties:
            return "looks like normal text"
        worst = sorted(self.penalties.items(), key=lambda kv: -kv[1])[:2]
        return ", ".join(f"{name} ({value:.2f})" for name, value in worst)


def text_quality(text: str) -> float:
    """Return a score from 0 (garbage) to 1 (clean text)."""
    return quality_report(text).score


def quality_report(text: str) -> QualityReport:
    """Like text_quality but also says what was wrong."""
    stripped = _CID.sub("\x00", text)  # each (cid:NN) counted as one junk character
    visible = [c for c in stripped if not c.isspace()]
    if not visible:
        return QualityReport(score=0.0, chars=0, penalties={"empty": 1.0})

    total = len(visible)
    penalties: dict[str, float] = {}

    junk = sum(1 for c in visible if c == "\x00" or c == "�" or _is_private_use(c))
    controls = sum(1 for c in visible if _is_control(c))
    _add(penalties, "junk characters", (junk + controls) / total * 2.0)

    # "too few letters": real text is mostly letters and digits.
    wordy = sum(1 for c in visible if c.isalnum())
    wordy_ratio = wordy / total
    if wordy_ratio < 0.6:
        _add(penalties, "too few letters", (0.6 - wordy_ratio) * 1.5)

    _add(penalties, "words without vowels", _no_vowel_ratio(stripped) * 1.2)
    _add(penalties, "odd character mixes", _odd_token_ratio(stripped) * 1.0)

    score = max(0.0, 1.0 - sum(penalties.values()))
    return QualityReport(score=round(score, 3), chars=total, penalties=penalties)


def _add(penalties: dict[str, float], name: str, value: float) -> None:
    if value > 0.01:
        penalties[name] = min(value, 1.0)


def _is_private_use(char: str) -> bool:
    return unicodedata.category(char) == "Co"


def _is_control(char: str) -> bool:
    return unicodedata.category(char) in ("Cc", "Cs", "Cn") and char != "\x00"


def _no_vowel_ratio(text: str) -> float:
    """Share of long letter-words (4+ letters) that have no vowel, e.g. 'Wkhq' or 'xzkrt'.

    ALL-CAPS words are skipped on purpose: abbreviations like 'MWD' or 'BHA' are normal
    in technical documents.
    """
    words = [w for w in _WORD.findall(text) if len(w) >= 4 and not w.isupper()]
    if not words:
        return 0.0
    bad = sum(1 for w in words if not any(ch in _VOWELS for ch in w))
    return bad / len(words)


def _odd_token_ratio(text: str) -> float:
    """Share of tokens that jump between letters, digits and symbols like 'a3$f9'."""
    tokens = _TOKEN.findall(text)
    if not tokens:
        return 0.0
    odd = sum(1 for t in tokens if _is_odd_token(t))
    return odd / len(tokens)


def _is_odd_token(token: str) -> bool:
    if len(token) < 4:
        return False
    classes = [_char_class(c) for c in token]
    switches = sum(1 for a, b in zip(classes, classes[1:], strict=False) if a != b)
    if switches >= 3 and "symbol" in classes:
        return True
    # letters from different alphabets glued in one word (Latin + Cyrillic ...)
    scripts = {_script(c) for c in token if c.isalpha()}
    return len(scripts) > 1


def _char_class(char: str) -> str:
    if char.isalpha():
        return "alpha"
    if char.isdigit():
        return "digit"
    if char in _TOKEN_PUNCT:
        return "punct"
    return "symbol"


def _script(char: str) -> str:
    try:
        return unicodedata.name(char).split(" ")[0]  # LATIN, CYRILLIC, GREEK, CJK, ...
    except ValueError:
        return "UNKNOWN"
