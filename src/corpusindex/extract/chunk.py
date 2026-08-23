import re

CHARS_PER_TOKEN = 4

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_MD_HEADING = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT.split(text.strip()) if p.strip()]


def _hard_split(s: str, size: int) -> list[str]:
    return [s[i : i + size] for i in range(0, len(s), size)] or [""]


def _seed_overlap(units: list[str], overlap_chars: int, joiner: str) -> tuple[list[str], int]:
    if overlap_chars <= 0 or not units:
        return [], 0
    seed: list[str] = []
    total = 0
    for unit in reversed(units):
        add = len(unit) + (len(joiner) if seed else 0)
        if seed and total + add > overlap_chars:
            break
        seed.insert(0, unit)
        total += add
        if total >= overlap_chars:
            break
    length = 0
    for i, u in enumerate(seed):
        length += len(u) + (len(joiner) if i > 0 else 0)
    return seed, length


def _pack_units(units: list[str], target_chars: int, overlap_chars: int, joiner: str = "\n\n") -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(joiner.join(current))
        current, current_len = _seed_overlap(current, overlap_chars, joiner)

    for unit in units:
        if len(unit) > target_chars:
            flush()
            chunks.extend(_hard_split(unit, target_chars))
            current, current_len = [], 0
            continue
        added_len = len(unit) + (len(joiner) if current else 0)
        if current and current_len + added_len > target_chars:
            flush()
            added_len = len(unit) + (len(joiner) if current else 0)
        current.append(unit)
        current_len += added_len

    if current:
        chunks.append(joiner.join(current))
    return chunks


def _split_and_pack(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []
    return _pack_units(paragraphs, target_chars, overlap_chars)


def _split_markdown_sections(text: str) -> list[str]:
    matches = list(_MD_HEADING.finditer(text))
    if not matches:
        return [text]
    sections: list[str] = []
    if matches[0].start() > 0:
        pre = text[: matches[0].start()].strip()
        if pre:
            sections.append(pre)
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end].strip()
        if section:
            sections.append(section)
    return sections


def chunk_prose(
    text: str,
    header: str,
    *,
    target_tokens: int = 1000,
    overlap_tokens: int = 100,
    markdown: bool = False,
) -> list[str]:
    text = text.strip()
    if not text:
        return []
    target_chars = target_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    if markdown:
        bodies: list[str] = []
        for section in _split_markdown_sections(text):
            bodies.extend(_split_and_pack(section, target_chars, overlap_chars))
    else:
        bodies = _split_and_pack(text, target_chars, overlap_chars)
    return [f"{header}\n\n{body}" for body in bodies]


def chunk_csv(
    text: str,
    header: str,
    *,
    target_tokens: int = 1000,
    overlap_tokens: int = 100,
) -> list[str]:
    lines = text.strip("\n").split("\n")
    if not lines or not lines[0]:
        return []
    csv_header_row = lines[0]
    data_lines = [line for line in lines[1:] if line != ""]
    if not data_lines:
        return [f"{header}\n\n{csv_header_row}"]
    target_chars = target_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    bodies = _pack_units(data_lines, target_chars, overlap_chars, joiner="\n")
    return [f"{header}\n\n{csv_header_row}\n{body}" for body in bodies]


def _split_code_units(text: str) -> list[str]:
    lines = text.split("\n")
    units: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line and not line[0].isspace() and current:
            units.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        units.append(current)
    return ["\n".join(u) for u in units]


def chunk_code(text: str, header: str, *, target_tokens: int = 1500) -> list[str]:
    text = text.strip("\n")
    if not text.strip():
        return []
    target_chars = target_tokens * CHARS_PER_TOKEN
    units: list[str] = []
    for unit in _split_code_units(text):
        if len(unit) > target_chars:
            units.extend(_split_paragraphs(unit) or [unit])
        else:
            units.append(unit)
    bodies = _pack_units(units, target_chars, overlap_chars=0, joiner="\n\n")
    return [f"{header}\n\n{body}" for body in bodies]


def chunk_whole(text: str, header: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [f"{header}\n\n{text}"]
