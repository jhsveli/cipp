import json
from importlib import metadata
from urllib.parse import urlparse

from .cmd import exec

REPO = "jhsveli/cipp"
CHECK_SECONDS = 60


def install_info() -> tuple[str, str] | None:
	# (running commit, update hint), from pip's direct_url.json: a git install
	# records the commit; an editable (dev) install is a local checkout. None if
	# unknown (e.g. installed from a plain directory).
	try:
		raw = metadata.distribution("cipp").read_text("direct_url.json")
		info = json.loads(raw or "{}")
	except Exception:
		return None
	commit = (info.get("vcs_info") or {}).get("commit_id")
	if commit:
		return commit, "pipx reinstall cipp"
	if (info.get("dir_info") or {}).get("editable"):
		path = urlparse(info.get("url", "")).path
		try:
			head = exec(["git", "-C", path, "rev-parse", "HEAD"]).strip()
		except Exception:
			return None
		return head, "git pull, restart"
	return None


def commits_behind(commit: str) -> int:
	# Commits on main not in `commit`. A commit unknown to GitHub (unpushed local
	# HEAD) raises; callers treat that as "no update".
	out = exec(["gh", "api", f"repos/{REPO}/compare/{commit}...main", "--jq", ".ahead_by"])
	return int(out.strip() or 0)


def checker():
	# -> callable returning the status-bar label ("" = up to date), or None
	# when the running commit is unknown (no check).
	info = install_info()
	if info is None:
		return None
	commit, hint = info

	def check() -> str:
		try:
			n = commits_behind(commit)
		except Exception:
			return ""
		return f"⬆ {n} new commit(s) — {hint}" if n > 0 else ""

	return check
