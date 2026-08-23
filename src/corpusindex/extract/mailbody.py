import re
from dataclasses import dataclass, field
from email.message import EmailMessage

from corpusindex.extract.html_text import html_to_text

_WROTE_TAIL_RE = re.compile(r"^\s*On\s.{0,200}?wrote:\s*$", re.IGNORECASE | re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class MailBody:
    text: str
    attachments: list[str] = field(default_factory=list)


def strip_quoted_history(text: str) -> str:
    match = _WROTE_TAIL_RE.search(text)
    if match:
        text = text[: match.start()]
    kept = [line for line in text.split("\n") if not line.lstrip().startswith(">")]
    result = "\n".join(kept)
    result = _BLANK_RUN.sub("\n\n", result)
    return result.strip()


def extract_body(msg: EmailMessage) -> MailBody:
    body_part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body_part is not None:
        content = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            content = html_to_text(content)
        text = strip_quoted_history(content)

    attachments = []
    for part in msg.iter_attachments():
        filename = part.get_filename()
        if filename:
            attachments.append(filename)

    return MailBody(text=text, attachments=attachments)
