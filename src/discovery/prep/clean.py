"""Cleaning: markup stripping, language detection, PII redaction, minimum-content filter.

`original_text` is never modified. Everything here produces `clean_text` and the flags
the later stages use to decide what reaches the model (architecture Section 6.2).
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

from discovery.config import PrepSettings

# Author-identifying keys that must not survive into items.metadata (PRIV-01).
USERNAME_KEYS = frozenset(
    {
        "username",
        "user_name",
        "user",
        "author",
        "author_name",
        "reviewer",
        "handle",
        "screen_name",
        "displayname",
        "display_name",
    }
)

# Function words that mark ordinary English, so a detector misfire does not drop it (CLN-04).
_ENGLISH_FUNCTION = frozenset(
    {
        "the",
        "and",
        "my",
        "this",
        "that",
        "with",
        "have",
        "was",
        "were",
        "but",
        "not",
        "for",
        "you",
        "your",
        "from",
        "they",
        "them",
        "just",
        "when",
        "what",
        "it's",
        "its",
        "can't",
        "don't",
        "very",
        "really",
        "because",
        "would",
        "could",
        "should",
        "about",
        "there",
        "their",
        "please",
    }
)

# Romanized Hindi tokens that are rare in English. Two or more in one text → code_mixed (CLN-03).
# Short tokens (se, ko, ki, ka) are omitted so ordinary English is not flagged.
_HINGLISH = frozenset(
    {
        "nahi",
        "nahin",
        "nhi",
        "hain",
        "mera",
        "meri",
        "mere",
        "mujhe",
        "kaise",
        "kyun",
        "kyon",
        "dhund",
        "dhundh",
        "dhoond",
        "dhoondh",
        "raha",
        "rahi",
        "rahe",
        "yaad",
        "kahan",
        "kahaan",
        "kaha",
        "apna",
        "apni",
        "apne",
        "abhi",
        "mila",
        "mili",
        "gayi",
        "gaya",
        "gaye",
        "wala",
        "wali",
        "wale",
        "karo",
        "karna",
        "bhai",
        "yaar",
        "mein",
        "mujhko",
        "kripya",
        "dhundo",
        "dhoondo",
    }
)

_EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_AADHAAR = re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}\b")
_BARE_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_PASSPORT = re.compile(r"\b[A-Z][0-9]{7}\b")
_PHONE_CANDIDATE = re.compile(r"(?<!\d)(\+?\d[\d\s().\-]{8,20}\d)(?!\d)")
_VERSION = re.compile(r"^\d{1,4}(?:\.\d{1,12}){2,}$")
_DATE = re.compile(r"^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})$")
_QUOTE_LINE = re.compile(r"^\s*>")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1", re.S)
_MD_EM = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_MD_CODE = re.compile(r"`([^`]*)`")
_MD_HEADING = re.compile(r"^#{1,6}\s*", re.M)
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_WORD = re.compile(r"\S+")
_SENTENCE = re.compile(r".+?(?:[.!?](?=\s|$)|$)", re.S)

_CHROME_LINE = re.compile(
    r"^(?:"
    r"menu|sign in(?: now)?|sign up|log in|close|skip to (?:content|search|main content)|"
    r"submenu|informational notification|this question has been marked as a duplicate\.?|"
    r"try again|something went wrong.*|details|locked|-\s*\[x\]|\[x\]"
    r")$",
    re.I,
)

# Emoji and emoji modifiers. Variation selectors and ZWJ are format characters, dropped below.
_EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "\U00002190-\U000021ff"
    "\U00002b00-\U00002bff"
    "\U0000fe0f"
    "\U0000200d"
    "]+"
)


@dataclass(frozen=True)
class CleanResult:
    clean_text: str
    language: str
    language_confidence: float
    code_mixed: bool
    word_count: int
    ai_eligible: bool
    exclude_reason: str | None
    truncated: bool
    pii_redactions: int
    dedup_text: str


def analysis_text(title: str | None, original_text: str, op_followups: list[str] | None) -> str:
    """Same composition as `Item.analysis_text`: title, body, original-poster follow-ups."""
    parts: list[str] = []
    if title and not original_text.startswith(title.strip()):
        parts.append(title.strip())
    parts.append(original_text)
    parts += [f"[Update from original poster] {t}" for t in op_followups or [] if t]
    return "\n\n".join(p for p in parts if p)


def phrase_pattern(phrases: list[str]) -> re.Pattern[str]:
    """Case-insensitive whole-phrase matcher. Longer phrases are tried first."""
    ordered = sorted(
        {_fold_apostrophe(p.strip().lower()) for p in phrases if p.strip()},
        key=len,
        reverse=True,
    )
    if not ordered:
        return re.compile(r"(?!)")
    body = "|".join(re.escape(p) for p in ordered)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.I)


def contains_phrase(text: str, pattern: re.Pattern[str]) -> bool:
    return pattern.search(_fold_apostrophe(text)) is not None


def scrub_usernames(metadata: dict) -> tuple[dict, list[str]]:
    """Drop raw usernames from metadata. Returns the cleaned mapping and the removed values."""
    removed: list[str] = []

    def walk(obj: object) -> object:
        if isinstance(obj, dict):
            out = {}
            for key, value in obj.items():
                if key.lower() in USERNAME_KEYS or key in USERNAME_KEYS:
                    if isinstance(value, str) and value.strip():
                        removed.append(value.strip())
                    continue
                out[key] = walk(value)
            return out
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    return walk(metadata), removed  # type: ignore[return-value]


def find_pii(text: str) -> list[str]:
    """Kinds of raw PII still present. Used to scan `clean_text` after redaction."""
    found: list[str] = []
    if _EMAIL.search(text):
        found.append("email")
    if _phone_spans(text):
        found.append("phone")
    return found


def clean(
    text: str,
    *,
    prep: PrepSettings,
    retrieval: re.Pattern[str],
    max_chars: int,
    strip_boilerplate: bool = False,
) -> CleanResult:
    """Produce `clean_text` and eligibility flags. `text` is left untouched by the caller."""
    working = _strip_markup(text)
    if strip_boilerplate:
        working = _strip_boilerplate(working)
    emoji_only = _is_emoji_only(working)
    # Names are what the model reads. Counts, language, and dedup use the text with emoji
    # removed, so a row of hearts is not treated as a long review.
    described = _normalize_ws(_replace_emoji(working))
    plain = _normalize_ws(_EMOJI.sub(" ", working))
    described, pii_redactions = _redact_pii(described)
    plain, _ = _redact_pii(plain)
    described = _normalize_ws(described)
    plain = _normalize_ws(plain)

    dedup_text = normalize_for_dedup(plain)
    word_count = len(_WORD.findall(plain))
    if plain:
        language, confidence, code_mixed = detect_language(
            plain,
            min_words=prep.language_min_words,
            min_confidence=prep.language_confidence,
        )
    else:
        language, confidence, code_mixed = "en", 0.5, False

    truncated = False
    if len(described) > max_chars:
        described = _truncate(
            described, head=prep.truncation_head_chars, limit=max_chars, retrieval=retrieval
        )
        truncated = True

    eligible, reason = _eligibility(
        described,
        keyword_text=plain,
        word_count=word_count,
        emoji_only=emoji_only,
        language=language,
        code_mixed=code_mixed,
        prep=prep,
        retrieval=retrieval,
    )
    return CleanResult(
        clean_text=described,
        language="code_mixed" if code_mixed else language,
        language_confidence=confidence,
        code_mixed=code_mixed,
        word_count=word_count,
        ai_eligible=eligible,
        exclude_reason=reason,
        truncated=truncated,
        pii_redactions=pii_redactions,
        dedup_text=dedup_text,
    )


def normalize_for_dedup(text: str) -> str:
    """Lowercased, punctuation-light form used for the exact-dedup hash and shingles."""
    folded = _fold_apostrophe(text.lower())
    folded = re.sub(r"[^\w\s']", " ", folded, flags=re.UNICODE)
    return " ".join(folded.split())


def detect_language(text: str, *, min_words: int, min_confidence: float) -> tuple[str, float, bool]:
    """`(language, confidence, code_mixed)`.

    Short or mostly-ASCII text defaults to English (CLN-04). Non-Latin scripts are
    detected from the characters. Romanized Hindi mixed with English is `code_mixed`.
    """
    words = [w.strip(".,!?;:\"'()[]") for w in text.lower().split()]
    words = [w for w in words if w]
    hinglish_hits = sum(1 for w in words if w in _HINGLISH)
    code_mixed = hinglish_hits >= 2

    script = _script_language(text)
    if code_mixed:
        return "code_mixed", 0.9 if hinglish_hits >= 3 else 0.7, True
    if script:
        return script, 0.95, False
    if len(words) < min_words:
        return "en", 0.5, False
    english_hits = sum(1 for w in words if w in _ENGLISH_FUNCTION)
    if english_hits >= 2 and _mostly_ascii(text):
        return "en", 0.8, False

    detected, prob = _langdetect(text)
    if detected and prob >= min_confidence and detected != "en":
        return detected, prob, False
    if _mostly_ascii(text) or detected == "en":
        return "en", max(prob, 0.5), False
    return detected or "unknown", prob, False


# --- steps -----------------------------------------------------------------------


def _fold_apostrophe(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")


def _strip_markup(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = html.unescape(text)
    # Reddit/forum quote blocks duplicate the parent post (CLN-08, DD-04).
    lines = [ln for ln in text.splitlines() if not _QUOTE_LINE.match(ln)]
    text = "\n".join(lines)
    text = _MD_IMAGE.sub(lambda m: m.group(1), text)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _HTML_TAG.sub(" ", text)
    text = _MD_EMPHASIS.sub(r"\2", text)
    text = _MD_EM.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _MD_CODE.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    return text


def _strip_boilerplate(text: str) -> str:
    """Drop site chrome from page captures (nav, sign-in, duplicate banners)."""
    kept: list[str] = []
    started = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if started:
                kept.append("")
            continue
        if _CHROME_LINE.match(stripped):
            continue
        if not started and _is_nav_line(stripped):
            continue
        started = True
        kept.append(stripped)
    return "\n".join(kept)


def _is_nav_line(line: str) -> bool:
    plain = _MD_LINK.sub("", _MD_IMAGE.sub("", line))
    plain = _URL.sub("", plain)
    plain = re.sub(r"[*#\-\[\]|]+", " ", plain)
    return len(plain.split()) < 3


def _is_emoji_only(text: str) -> bool:
    stripped = _EMOJI.sub("", text)
    return not any(ch.isalnum() for ch in stripped)


def _replace_emoji(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        names: list[str] = []
        for ch in match.group(0):
            # Format characters (ZWJ) and marks (variation selectors) are not words.
            if unicodedata.category(ch)[0] in {"C", "M"}:
                continue
            try:
                names.append(unicodedata.name(ch).lower())
            except ValueError:
                continue
        return (" " + " ".join(names) + " ") if names else " "

    return _EMOJI.sub(repl, text)


def _normalize_ws(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    text = _WS.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _redact_pii(text: str) -> tuple[str, int]:
    count = 0

    def sub(pattern: re.Pattern[str], repl: str, value: str) -> str:
        nonlocal count
        value, n = pattern.subn(repl, value)
        count += n
        return value

    text = sub(_EMAIL, "[EMAIL]", text)
    text = sub(_URL, "[URL]", text)
    # IDs before phones so a grouped Aadhaar number is not labeled as a phone (CLN-07).
    text = sub(_AADHAAR, "[ID]", text)
    text = sub(_BARE_ID, "[ID]", text)
    text = sub(_PASSPORT, "[ID]", text)
    text, n_phone = _redact_phones(text)
    count += n_phone
    return text, count


def _phone_spans(text: str) -> list[str]:
    found = []
    for match in _PHONE_CANDIDATE.finditer(text):
        raw = match.group(1).strip()
        if _looks_like_phone(raw):
            found.append(raw)
    return found


def _redact_phones(text: str) -> tuple[str, int]:
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        raw = match.group(1).strip()
        if not _looks_like_phone(raw):
            return match.group(0)
        count += 1
        return "[PHONE]"

    return _PHONE_CANDIDATE.sub(repl, text), count


def _looks_like_phone(raw: str) -> bool:
    compact = re.sub(r"\s", "", raw)
    if _VERSION.match(compact) or _DATE.match(compact):
        return False
    if re.fullmatch(r"\d{1,4}(?:\.\d{1,12}){2,}", compact):
        return False
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 15:
        return False
    # A bare 12-digit run is an ID (Aadhaar), not a phone (CLN-07).
    return not (len(digits) == 12 and raw.strip().isdigit())


def _script_language(text: str) -> str | None:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return None
    counts: dict[str, int] = {}
    for ch in letters:
        code = ord(ch)
        if 0x0900 <= code <= 0x097F:
            key = "hi"
        elif 0x0980 <= code <= 0x09FF:
            key = "bn"
        elif 0x0B80 <= code <= 0x0BFF:
            key = "ta"
        elif 0x0C00 <= code <= 0x0C7F:
            key = "te"
        elif 0x0C80 <= code <= 0x0CFF:
            key = "kn"
        elif 0x0D00 <= code <= 0x0D7F:
            key = "ml"
        elif 0x0600 <= code <= 0x06FF:
            key = "ar"
        elif 0x0400 <= code <= 0x04FF:
            key = "ru"
        elif 0x4E00 <= code <= 0x9FFF:
            key = "zh"
        elif ch.isascii():
            key = "latin"
        else:
            key = "other"
        counts[key] = counts.get(key, 0) + 1
    key, n = max(counts.items(), key=lambda kv: kv[1])
    if key != "latin" and n / len(letters) >= 0.3:
        return key
    return None


def _mostly_ascii(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    return sum(c.isascii() for c in letters) / len(letters) >= 0.9


def _langdetect(text: str) -> tuple[str | None, float]:
    try:
        from langdetect import DetectorFactory, detect_langs
        from langdetect.lang_detect_exception import LangDetectException
    except ImportError:
        return None, 0.0
    DetectorFactory.seed = 0
    try:
        langs = detect_langs(text)
    except LangDetectException:
        return None, 0.0
    if not langs:
        return None, 0.0
    top = langs[0]
    return top.lang, float(top.prob)


def _truncate(text: str, *, head: int, limit: int, retrieval: re.Pattern[str]) -> str:
    """Keep the opening plus later sentences that carry a retrieval keyword (CLN-10)."""
    head_text = text[:head].rsplit(" ", 1)[0] if len(text) > head else text
    rest = text[len(head_text) :]
    kept = [head_text.strip()]
    for sentence in _SENTENCE.findall(rest):
        piece = sentence.strip()
        if piece and contains_phrase(piece, retrieval):
            kept.append(piece)
        if sum(len(p) + 1 for p in kept) >= limit:
            break
    out = "\n".join(p for p in kept if p)
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip()
    return out


def _eligibility(
    text: str,
    *,
    keyword_text: str,
    word_count: int,
    emoji_only: bool,
    language: str,
    code_mixed: bool,
    prep: PrepSettings,
    retrieval: re.Pattern[str],
) -> tuple[bool, str | None]:
    if emoji_only:
        return False, "emoji_only"
    if not text or word_count == 0:
        return False, "empty"
    # D6: code-mixed text is flagged and kept out of the AI stages. Other languages too,
    # unless they are listed in prep.languages.
    if code_mixed or language == "code_mixed":
        return False, "code_mixed"
    if language not in prep.languages:
        return False, "language"
    if word_count < prep.min_words and not contains_phrase(keyword_text, retrieval):
        return False, "min_words"
    return True, None
