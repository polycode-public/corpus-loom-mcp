from pathlib import PurePath
from typing import Literal

DECODER_EXTENSIONS = frozenset({
    ".txt", ".csv", ".md", ".html", ".htm", ".css", ".js", ".xml", ".sh", ".ini",
})

CODE_EXTENSIONS = frozenset({
    ".py", ".java", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rb", ".c",
    ".h", ".cpp", ".cc", ".hpp", ".cs", ".php", ".rs", ".swift", ".kt", ".kts",
    ".scala", ".sql", ".yaml", ".yml", ".json", ".toml", ".gradle", ".m",
    ".mm", ".pl", ".pm", ".lua", ".r", ".jsp", ".vue", ".svelte",
})

CONVERT_EXTENSIONS = frozenset({
    ".pdf", ".doc", ".docx", ".rtf", ".odt",
})

METADATA_ONLY_EXTENSIONS = frozenset({
    ".xls", ".xlsx", ".ods",
})

Classification = Literal["decode", "convert", "metadata"]

NUL_SNIFF_WINDOW = 8192


def classify(path: str | PurePath) -> Classification:
    ext = PurePath(path).suffix.lower()
    if ext in DECODER_EXTENSIONS or ext in CODE_EXTENSIONS:
        return "decode"
    if ext in METADATA_ONLY_EXTENSIONS:
        return "metadata"
    if ext in CONVERT_EXTENSIONS:
        return "convert"
    return "metadata"


def is_code_extension(path: str | PurePath) -> bool:
    return PurePath(path).suffix.lower() in CODE_EXTENSIONS


def is_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:NUL_SNIFF_WINDOW]


def decode_text(raw: bytes, declared_charset: str | None = None) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if declared_charset:
        try:
            return raw.decode(declared_charset)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("latin-1")
