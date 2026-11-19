"""Ground a model quote in the exact source span the founder wrote.

Comparison is forgiving. Storage is not: a match is replaced by the original
slice, and an invented quote still fails.
"""
from __future__ import annotations

import unicodedata

from .stores import BrainError

MANUAL_INTERVIEW_ATTEMPTS = 3
MANUAL_INTERVIEW_MESSAGE = (
    "A leitura automática desta resposta foi recusada várias vezes. "
    "Não repita o mesmo pedido. Use operator prompt e operator apply com o assistente, "
    "ou operator commit-literal para guardar o texto como foi escrito."
)
MANUAL_COMMANDS = (
    'python3 -m company_pilot --config "$PWD/.company-pilot-local/company.json" operator prompt --output "$PWD/.company-pilot-local/pedido.json"',
    'python3 -m company_pilot --config "$PWD/.company-pilot-local/company.json" operator apply --file "$PWD/.company-pilot-local/analise.json"',
    'python3 -m company_pilot --config "$PWD/.company-pilot-local/company.json" operator commit-literal',
)

_QUOTE_CHARS = set("\"'«»“”„‟‘’‚‛‹›`´")
_DASH_CHARS = set("-–—―−‐‑‒")
_MINOR_PUNCT = set(".,;:!?¡¿()[]·•")
_EXPAND = _QUOTE_CHARS | _MINOR_PUNCT | {"…"}
_QUOTE_LIMIT = 400
_FAILURE_LIMIT = 8
_FUZZY_MIN_CHARS = 20
_FUZZY_MIN_WORDS = 4
_FUZZY_MAX_EDITS = 2


def bound_quote_failures(failures):
    """Closed, short retry hints. Never copy arbitrary model or exception text."""
    if not isinstance(failures, list):
        return []
    bounded = []
    for item in failures:
        if len(bounded) == _FAILURE_LIMIT:
            break
        if not isinstance(item, dict):
            continue
        quote = item.get("quote")
        source_id = item.get("source_id")
        bounded.append({
            "source_id": source_id[:80] if isinstance(source_id, str) else "",
            "quote": quote[:_QUOTE_LIMIT] if isinstance(quote, str) else "",
            "reason": "source_quote_not_found",
        })
    return bounded


def quote_not_found(failures):
    error = BrainError("source_quote_not_found")
    error.quote_failures = bound_quote_failures(failures)
    return error


def awaiting_manual_interview(job):
    return (isinstance(job, dict) and job.get("kind") == "interview" and job.get("status") == "pending"
            and job.get("error_code") == "analysis_rejected"
            and type(job.get("attempts")) is int and job["attempts"] >= MANUAL_INTERVIEW_ATTEMPTS)


def manual_interview_route(jobs):
    chosen = [job for job in jobs or [] if awaiting_manual_interview(job)]
    if not chosen:
        return None
    job = max(chosen, key=lambda item: item["attempts"])
    return {"recommended_route": "manual", "job_id": job.get("id"), "attempts": job["attempts"],
            "rejection_reason": job.get("rejection_reason"), "message": MANUAL_INTERVIEW_MESSAGE,
            "commands": list(MANUAL_COMMANDS)}


def locate_source_span(source_text, quote):
    """Return the original slice that the quote names, or None."""
    if not isinstance(source_text, str) or not isinstance(quote, str) or not quote.strip():
        return None
    if quote in source_text:
        return quote
    norm_source, spans = _normalize(source_text)
    norm_quote, _quote_spans = _normalize(quote)
    if not norm_quote or not norm_source:
        return None
    index = norm_source.find(norm_quote)
    if index >= 0:
        return _slice(source_text, spans, index, index + len(norm_quote))
    return _fuzzy_span(source_text, norm_source, spans, norm_quote)


def _expand(source_text, start, end):
    """Keep wrapping punctuation the founder wrote on the same passage."""
    while start > 0 and source_text[start - 1] in _EXPAND:
        start -= 1
    while end < len(source_text) and source_text[end] in _EXPAND:
        end += 1
    return source_text[start:end]


def _slice(source_text, spans, start, end):
    if end <= start:
        return None
    span = _expand(source_text, spans[start][0], spans[end - 1][1])
    if not span or span not in source_text:
        return None
    return span


def _is_space(char):
    return char.isspace() or unicodedata.category(char).startswith("Z")


def _normalize(text):
    """NFC/NFKC, casefold, unified dashes, collapsed space, minor punctuation removed.

    Internal punctuation such as the dot in v1.2 stays, so a deleted dot cannot
    join two tokens into a different number.
    """
    pieces = []
    origins = []
    index = 0
    size = len(text)
    while index < size:
        end = index + 1
        while end < size and unicodedata.category(text[end]).startswith("M"):
            end += 1
        chunk = unicodedata.normalize("NFKC", unicodedata.normalize("NFC", text[index:end]))
        for char in chunk:
            for folded in char.casefold():
                if unicodedata.category(folded) == "Cf" or folded == "\u2026":
                    continue
                if folded in _DASH_CHARS:
                    folded = "-"
                pieces.append(folded)
                origins.append((index, end))
        index = end
    kept = []
    spans = []
    for position, char in enumerate(pieces):
        if char in _QUOTE_CHARS:
            continue
        if char in _MINOR_PUNCT and not _internal_punctuation(pieces, position):
            continue
        if _is_space(char):
            if kept and kept[-1] != " ":
                kept.append(" ")
                spans.append(origins[position])
            elif kept and kept[-1] == " ":
                start, _old_end = spans[-1]
                spans[-1] = (start, origins[position][1])
            continue
        kept.append(char)
        spans.append(origins[position])
    if kept and kept[-1] == " ":
        kept.pop()
        spans.pop()
    return "".join(kept), spans


def _internal_punctuation(pieces, position):
    previous = pieces[position - 1] if position else ""
    following = pieces[position + 1] if position + 1 < len(pieces) else ""
    return previous.isalnum() and following.isalnum()


def _words(norm, spans):
    words = []
    index = 0
    size = len(norm)
    while index < size:
        if norm[index] == " ":
            index += 1
            continue
        end = index
        while end < size and norm[end] != " ":
            end += 1
        words.append((norm[index:end], spans[index][0], spans[end - 1][1]))
        index = end
    return words


def _bounded_distance(left, right, limit):
    if abs(len(left) - len(right)) > limit:
        return None
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, 1):
        current = [index]
        row_min = index
        for column, right_char in enumerate(right, 1):
            insert = current[column - 1] + 1
            delete = previous[column] + 1
            replace = previous[column - 1] + (left_char != right_char)
            best = insert if insert < delete else delete
            if replace < best:
                best = replace
            current.append(best)
            if best < row_min:
                row_min = best
        if row_min > limit:
            return None
        previous = current
    return previous[-1] if previous[-1] <= limit else None


def _fuzzy_span(source_text, norm_source, spans, norm_quote):
    """One unique typo-level alignment. Ambiguous or distant spans do not match."""
    if len(norm_quote) < _FUZZY_MIN_CHARS:
        return None
    source_words = _words(norm_source, spans)
    quote_words = [word for word, _start, _end in _words(norm_quote, [(0, 0)] * len(norm_quote))]
    if len(quote_words) < _FUZZY_MIN_WORDS:
        return None
    width = len(quote_words)
    found = []
    for start_word in range(len(source_words) - width + 1):
        total = 0
        changed = 0
        aligned = True
        for offset, quote_word in enumerate(quote_words):
            source_word = source_words[start_word + offset][0]
            if source_word == quote_word:
                continue
            if len(source_word) < 5 or len(quote_word) < 5:
                aligned = False
                break
            distance = _bounded_distance(source_word, quote_word, _FUZZY_MAX_EDITS)
            if not distance:
                aligned = False
                break
            total += distance
            changed += 1
            if changed > _FUZZY_MAX_EDITS or total > _FUZZY_MAX_EDITS:
                aligned = False
                break
        if not aligned or total == 0 or total > _FUZZY_MAX_EDITS:
            continue
        start = source_words[start_word][1]
        end = source_words[start_word + width - 1][2]
        span = _expand(source_text, start, end)
        if span and span in source_text and span not in found:
            found.append(span)
        if len(found) > 1:
            return None
    return found[0] if len(found) == 1 else None
