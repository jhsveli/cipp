import re

_AUTHOR_LINE = re.compile(r"^Author:\s*([^<\n]+?)\s*(?:<.*)?$", re.MULTILINE)
_PAREN = re.compile(r"\(([^)]+)\)")
_PR_REF = re.compile(r"^#\d+$")


def from_body(body: str) -> str | None:
	m = _AUTHOR_LINE.search(body or "")
	return m.group(1) if m else None


def from_title(title: str) -> str | None:
	matches = [m.group(1) for m in _PAREN.finditer(title or "")]
	matches = [m for m in matches if not _PR_REF.match(m)]
	return matches[-1] if matches else None


def extract_author(pr: dict) -> str:
	return from_title(pr.get("title", "")) or from_body(pr.get("body", "")) or pr["author"]
