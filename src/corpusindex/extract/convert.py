import shutil
import subprocess
from pathlib import Path

_TIMEOUT_SECONDS = 60


def _run_capture(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        text = result.stdout.decode("latin-1")
    text = text.strip()
    return text or None


def convert_pdf(path: str | Path) -> str | None:
    if shutil.which("pdftotext") is None:
        return None
    return _run_capture(["pdftotext", "-layout", str(path), "-"])


def _convert_via_textutil(path: str | Path) -> str | None:
    if shutil.which("textutil") is None:
        return None
    return _run_capture(["textutil", "-convert", "txt", "-stdout", str(path)])


def _convert_via_python_docx(path: str | Path) -> str | None:
    try:
        import docx
    except ImportError:
        return None
    try:
        document = docx.Document(str(path))
        text = "\n\n".join(p.text for p in document.paragraphs)
    except Exception:
        return None
    text = text.strip()
    return text or None


def convert_doc(path: str | Path) -> str | None:
    text = _convert_via_textutil(path)
    if text:
        return text
    if Path(path).suffix.lower() == ".docx":
        return _convert_via_python_docx(path)
    return None


def convert(path: str | Path) -> str | None:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return convert_pdf(path)
    if ext in (".doc", ".docx", ".rtf"):
        return convert_doc(path)
    return None
