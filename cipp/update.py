import json
import shutil
from dataclasses import dataclass
from importlib import metadata
from typing import Callable
from urllib.parse import urlparse

from .cmd import exec

REPO = "jhsveli/cipp"
CHECK_SECONDS = 60


@dataclass
class Updater:
	# `check() -> commits behind main` (raises = unknown, treated as up to date).
	# `apply()` self-updates in place (blocking, raises on failure); None = the
	# user updates by hand per `hint`.
	check: Callable[[], int]
	apply: Callable[[], None] | None
	hint: str
	seconds: int = CHECK_SECONDS


def install_info() -> tuple[str, bool] | None:
	# (running commit, is git install), from pip's direct_url.json: a git install
	# records the commit; an editable (dev) install is a local checkout. None if
	# unknown (e.g. installed from a plain directory).
	try:
		raw = metadata.distribution("cipp").read_text("direct_url.json")
		info = json.loads(raw or "{}")
	except Exception:
		return None
	commit = (info.get("vcs_info") or {}).get("commit_id")
	if commit:
		return commit, True
	if (info.get("dir_info") or {}).get("editable"):
		path = urlparse(info.get("url", "")).path
		try:
			head = exec(["git", "-C", path, "rev-parse", "HEAD"]).strip()
		except Exception:
			return None
		return head, False
	return None


def commits_behind(commit: str) -> int:
	# Commits on main not in `commit`. A commit unknown to GitHub (unpushed local
	# HEAD) raises.
	out = exec(["gh", "api", f"repos/{REPO}/compare/{commit}...main", "--jq", ".ahead_by"])
	return int(out.strip() or 0)


def pipx_upgrade() -> None:
	# In-place pip upgrade: a failed fetch leaves the running install intact
	# (unlike `pipx reinstall`, which deletes the venv first). Works because the
	# version comes from git (hatch-vcs), so every commit on main is newer.
	exec(["pipx", "upgrade", "cipp"])


def updater() -> Updater | None:
	# None when the running commit is unknown (no check). Editable dev installs
	# are never self-updated: a pull could clash with local edits.
	info = install_info()
	if info is None:
		return None
	commit, git_install = info
	can_apply = git_install and shutil.which("pipx") is not None
	return Updater(
		check=lambda: commits_behind(commit),
		apply=pipx_upgrade if can_apply else None,
		hint="pipx upgrade cipp" if git_install else "git pull, restart",
	)


def version() -> str:
	try:
		return metadata.version("cipp")
	except Exception:
		return ""
