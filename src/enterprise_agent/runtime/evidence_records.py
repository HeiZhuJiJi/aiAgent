"""Source-context preserving record selection; no publisher or topic rules."""
import re
from dataclasses import dataclass

from enterprise_agent.runtime.research_scope import dated_text_records


@dataclass(frozen=True)
class Record:
    context: str
    text: str
    stamp: tuple[int, int, int]


def _year_heading(line: str) -> bool:
    return bool(len(line.strip()) <= 160 and re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", line)
                and re.search(r"[a-zA-Z\u3400-\u9fff]", line)
                and not re.match(r"\s*(?:19|20)\d{2}[-/]\d", line))


def matching_records(content: str, query: str) -> tuple[Record, ...]:
    requested = re.findall(r"((?:19|20)\d{2})[-/\u5e74](\d{1,2})[-/\u6708](\d{1,2})", query)
    bounds = sorted(tuple(map(int, stamp)) for stamp in requested)
    if not bounds:
        return ()
    dates = dated_text_records(content)
    headings = []
    offset = 0
    date_starts = {start for start, _, _ in dates}
    for line in content.splitlines(keepends=True):
        if offset not in date_starts and _year_heading(line):
            headings.append(offset)
        offset += len(line)
    result = []
    for start, end, stamp in dates:
        if stamp is None or not bounds[0] <= stamp <= bounds[-1]:
            continue
        # A new section heading belongs to the next record, never this row.
        end = next((value for value in headings if start < value < end), end)
        context_start = max((value for value in headings if value < start), default=0)
        context_end = next((value for value, _, _ in dates if context_start <= value <= start), start)
        context = content[context_start:context_end].strip()
        if context_start == 0 and not any(value < start for value in headings):
            # Numeric prose before the column headings is a separate summary,
            # not a unit/header for this record. Retain the following labels.
            lines = context.splitlines()
            last_summary = max((index for index, line in enumerate(lines)
                                if re.search(r"\d", line) and "|" not in line), default=-1)
            context = "\n".join(lines[last_summary + 1:])
        if len(context) > 240:
            context = context[:80] + "\n...\n" + context[-140:]
        result.append(Record(context=context, text=content[start:end].strip(), stamp=stamp))
    return tuple(result)


def pack_records(content: str, query: str, limit: int) -> str | None:
    records = matching_records(content, query)
    if not records:
        return None
    groups: dict[str, list[Record]] = {}
    for record in records:
        groups.setdefault(record.context, []).append(record)
    # Ranking is only a hint; retained records always retain their own scope.
    terms = set(re.findall(r"[a-z]{3,}|[\u3400-\u9fff]{2}", query.casefold()))
    ordered = sorted(groups.items(), key=lambda group: -sum(term in group[0].casefold() for term in terms))
    sections: list[str] = []
    for context, rows in ordered:
        kept: list[str] = []
        for row in rows:
            section = "\n".join(filter(None, (context, *kept, row.text)))
            if len("\n...\n".join((*sections, section))) <= limit:
                kept.append(row.text)
        if kept:
            sections.append("\n".join(filter(None, (context, *kept))))
    return "\n...\n".join(sections) if sections else None


def record_manifest(content: str, excerpt: str, query: str) -> dict[str, object]:
    records = matching_records(content, query)
    def compact(text: str) -> str:
        return re.sub(r"\s+", "", text)
    visible = compact(excerpt)
    included = [record for record in records if compact(record.text) in visible
                and (not record.context or compact(record.context) in visible)]
    def stamp(record: Record) -> str:
        return f"{record.stamp[0]:04d}-{record.stamp[1]:02d}-{record.stamp[2]:02d}"
    return {"matching_records_in_source": len(records), "complete_records_in_excerpt": len(included),
            "included_dates": sorted({stamp(record) for record in included}),
            "omitted_dates": sorted({stamp(record) for record in records if record not in included}),
            "scope_note": "Textual date matches only. Verify each record's own entity, section and units; "
            "omitted records were excluded from this excerpt, not absent from the source."}


def structured_blocks(content: str) -> tuple[tuple[int, int], ...]:
    """Locate whole serialized HTML lists, without pairing parallel lists or values."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for match in re.finditer(r"(?m)^</?(?:ul|ol)>$", content):
        if match.group().startswith('</'):
            if depth:
                depth -= 1
                if not depth:
                    spans.append((start, match.end()))
        else:
            if not depth:
                start = match.start()
            depth += 1
    # Adjacent parallel lists form one indivisible context block. Their order is
    # preserved, but equal lengths alone never assert a semantic relationship.
    groups: list[tuple[int, int]] = []
    for start, end in spans:
        if groups and not content[groups[-1][1]:start].strip():
            groups[-1] = (groups[-1][0], end)
        else:
            groups.append((start, end))
    return tuple(groups)


def pack_structured_blocks(content: str, query: str, limit: int) -> str | None:
    terms = set(re.findall(r"[a-z0-9]{2,}", query.casefold()))
    for phrase in re.findall(r"[\u3400-\u9fff]{2,}", query):
        terms.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    candidates = []
    spans = structured_blocks(content)
    for start, end in spans:
        # Preserve nearby heading context without synthesizing field labels.
        prefix = content[:start].rstrip()
        heading_start = max(0, prefix.rfind('\n', 0, max(0, prefix.rfind('\n'))))
        heading = prefix[heading_start:].strip()
        if len(heading) > 300 or '</ul>' in heading or '</ol>' in heading:
            heading = ''
        block = '\n'.join(filter(None, (heading, content[start:end])))
        # A long menu must not accumulate relevance across unrelated labels.
        # Rank by the best coherent line, just as prose is ranked below.
        score = max((sum(term in line.casefold() for term in terms)
                     for line in content[start:end].splitlines()), default=0)
        if score:
            candidates.append((score, start, block))
    # A matching navigation/list label must not displace an explanatory paragraph.
    # Compare literal query coverage outside lists, without topic or host rules.
    prose = content
    for start, end in reversed(spans):
        prose = prose[:start] + "\n" + prose[end:]
    prose_score = max((sum(term in line.casefold() for term in terms)
                       for line in prose.splitlines() if len(line.strip()) >= 100), default=0)
    if candidates and prose_score and prose_score >= max(item[0] for item in candidates):
        return None
    kept: list[tuple[int, str]] = []
    for _, start, block in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if sum(len(value) + 5 for _, value in kept) + len(block) <= limit:
            kept.append((start, block))
    return '\n...\n'.join(value for _, value in sorted(kept)) if kept else None


def excerpt_manifest(content: str, excerpt: str) -> dict[str, object]:
    spans = structured_blocks(content)
    included = sum(content[start:end] in excerpt for start, end in spans)
    return {'source_characters': len(content), 'excerpt_characters': len(excerpt),
            'source_fully_included': content in excerpt,
            'structured_blocks_in_source': len(spans), 'complete_blocks_in_excerpt': included,
            'omitted_blocks': len(spans) - included,
            'scope_note': 'Source means the retained evidence body, not necessarily the entire remote page. '
                          'Omission describes this excerpt, not absence from the source. '
                          'Read retained source content before searching again. List boundaries '
                          'preserve order only; do not invent labels, units or pairings.'}


def pack_table_blocks(content: str, query: str, limit: int) -> str | None:
    """Keep contiguous table rows and source context verbatim, never infer cells."""
    lines = content.splitlines(keepends=True)
    groups: list[tuple[int, int]] = []
    start: int | None = None
    offset = 0
    count = 0
    for line in [*lines, "\n"]:
        if " | " in line or line.startswith("| "):
            if start is None:
                start = offset
            count += 1
        elif start is not None:
            if count >= 3:
                groups.append((start, offset))
            start, count = None, 0
        offset += len(line)
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u3400-\u9fff]{2}", query.casefold()))
    candidates = []
    for start, end in groups:
        # The source introduction may contain identity, issue time, timezone and units.
        context = content[:min(start, 1100)].rstrip()
        block = content[start:end].rstrip()
        value = context + ("\n...\n" if start > 1100 else "\n") + block
        score = sum(term in value.casefold() for term in terms)
        if score and len(value) <= limit:
            candidates.append((score, len(block), value))
    return max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None
