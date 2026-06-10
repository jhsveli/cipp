import re

_AUTHOR_LINE = re.compile(r"^Author:\s*([^<\n]+?)\s*(?:<.*)?$", re.MULTILINE)
_PAREN = re.compile(r"\(([^)]+)\)")
_PR_REF = re.compile(r"^#\d+$")

KNOWN_BOTS = {
	"aws-plattform-image-updater": "image-updater",
	"dependabot": "dependabot",
}


def from_body(body: str) -> str | None:
	m = _AUTHOR_LINE.search(body or "")
	return m.group(1) if m else None


def from_title(title: str) -> list[str]:
	matches = [m.group(1) for m in _PAREN.finditer(title or "")]
	matches = [m for m in matches if not _PR_REF.match(m)]
	if not matches:
		return []
	# The last paren group holds the author(s); it may be a comma-separated list.
	return [a.strip() for a in matches[-1].split(",") if a.strip()]


def resolve_author(raw: str, is_bot: bool = False) -> tuple[str, bool]:
	if "[bot]" in raw:
		return raw.replace("[bot]", "").strip(), True
	if raw in KNOWN_BOTS:
		return KNOWN_BOTS[raw], True
	return raw, is_bot


def extract_authors(pr: dict) -> list[tuple[str, bool]]:
	raws = from_title(pr.get("title", ""))
	if not raws:
		body = from_body(pr.get("body", ""))
		raws = [body] if body else [pr["author"]]
	return [resolve_author(r) for r in raws]


def extract_author(pr: dict) -> tuple[str, bool]:
	return extract_authors(pr)[0]
