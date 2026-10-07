"""Query wording may change; the named target scope must not disappear."""
import re
import unicodedata
from datetime import date


def anchor_query(query: str, entity_scope: tuple[str, ...],
                 entity_aliases: dict[str, tuple[str, ...]] | None = None) -> str:
    def identity(value: str) -> str:
        return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())
    anchors = tuple(dict.fromkeys(x.strip() for x in entity_scope if x.strip()))
    names = (*anchors, *(alias for target, aliases in (entity_aliases or {}).items()
                           if target in anchors for alias in aliases if alias.strip()))
    if not anchors or any(identity(item) in identity(query) for item in names):
        return query.strip()
    # Only explicitly declared full aliases can replace the canonical search wording.
    # Actual source identity must still pass independent evidence review.
    prefix = ' '.join(anchors)
    return (prefix + ' ' + query.strip())[:1000]


def undated_discovery_query(query: str, entity_scope: tuple[str, ...]) -> str | None:
    """Broaden an empty temporal query, preserving dates that identify the target."""
    pattern = re.compile(r"(?:19|20)\d{2}\s*(?:\u5e74|[-/])\s*\d{1,2}\s*(?:\u6708|[-/])\s*\d{1,2}\s*\u65e5?")
    def replace(match: re.Match[str]) -> str:
        stamp = re.sub(r"\D", "", match.group())
        if any(stamp in re.sub(r"\D", "", item) for item in entity_scope):
            return match.group()
        return " "
    rewritten = pattern.sub(replace, query)
    if rewritten != query:
        # Remove a shortened end date only after stripping its full start date.
        # Target/version identities were protected above.
        rewritten = re.sub(r"(?:\u81f3|\u5230|through|to)\s*\d{1,2}\s*\u6708\s*\d{1,2}\s*\u65e5?", " ", rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r"(?:\u81f3|\u5230|through|to)\s*(?=$)", " ", rewritten, flags=re.IGNORECASE)
    result = re.sub(r"\s+", " ", rewritten).strip()
    return result if result and result != query.strip() else None


def source_identity_context(content: str, limit: int = 500) -> str:
    """Retain source breadcrumb context that relevance/date excerpts can discard."""
    lines = content.splitlines()
    starts = []
    marker = re.compile(r"^(?:\u5f53\u524d(?:\u4f4d\u7f6e)?|\u60a8(?:\u73b0\u5728)?\u7684\u4f4d\u7f6e|\u4f4d\u7f6e|you are here|breadcrumbs?|location)\s*[:\uff1a]", re.IGNORECASE)
    for index, line in enumerate(lines):
        if marker.search(line.strip()):
            starts.append(index)
    if not starts:
        starts = [i for i, line in enumerate(lines)
                  if line.count('>') >= 2 or line.count('\u203a') >= 2]
    excerpts = []
    for start in starts[:2]:
        excerpts.append('\n'.join(lines[start:start + 6]))
    return '\n'.join(excerpts)[:limit]


def dated_text_records(content: str) -> tuple[tuple[int, int, tuple[int, int, int] | None], ...]:
    """Locate date-led records; short dates inherit only an explicit source year.

    No year is inferred from the question or clock. Ambiguous year headings clear
    inherited context. Spans retain original cells and stop at the next record.
    """
    full = re.compile(r"^\s*(?:(?P<y>(?:19|20)\d{2})[-/\u5e74](?P<m>\d{1,2})[-/\u6708](?P<d>\d{1,2})(?:\u65e5)?|(?P<usm>\d{1,2})/(?P<usd>\d{1,2})/(?P<usy>(?:19|20)\d{2}))(?=[ \t\r\n|]|$)")
    short = re.compile(r"^\s*(?P<m>\d{1,2})[-/\u6708](?P<d>\d{1,2})(?:\u65e5)?(?=[ \t\r\n|]|$)")
    year_context = None
    starts: list[tuple[int, int, tuple[int, int, int] | None]] = []
    offset = 0
    for line in content.splitlines(keepends=True):
        match = full.match(line)
        partial = short.match(line) if match is None else None
        stamp = None
        if match is not None:
            year, month, day = (int(match['y'] or match['usy']), int(match['m'] or match['usm']), int(match['d'] or match['usd']))
        elif partial is not None and year_context is not None:
            year, month, day = year_context, int(partial['m']), int(partial['d'])
        else:
            year, month, day = 0, 0, 0
        if match is not None or partial is not None:
            try:
                value = date(year, month, day)
                stamp = (value.year, value.month, value.day)
            except ValueError:
                pass
            starts.append((offset, offset + len(line), stamp))
        else:
            # Only short heading-like lines establish a year; prose may mention
            # several unrelated years and is not a reliable table header.
            years = set(re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", line))
            if years and len(line.strip()) <= 160 and (not starts or re.search(r"[a-zA-Z\u3400-\u9fff]", line)):
                year_context = int(next(iter(years))) if len(years) == 1 else None
        offset += len(line)
    return tuple((start, starts[i + 1][0] if i + 1 < len(starts) else min(len(content), line_end + 220), stamp)
                 for i, (start, line_end, stamp) in enumerate(starts))
