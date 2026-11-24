import argparse
import os
import sys

from . import apps, claude, dbs, mine, prod, prs, update
from .pr_menu import RESTART, run_pr_menu

DEFAULT_UPDATE_INTERVAL = 30
# --tab name -> (tab, enabled), in tab order. Disabled tabs are hidden.
def all_tabs():
	return [
		("mine", mine.TAB, True),
		("reviews", prs.TAB, True),
		("prod", prod.TAB, True),
		# Apps / DBs need shifterctl.
		("apps", apps.TAB, apps.enabled()),
		("dbs", dbs.TAB, apps.enabled()),
		("claude", claude.TAB, claude.enabled()),
	]


TAB_NAMES = ["mine", "reviews", "prod", "apps", "dbs", "claude"]


def update_interval() -> int:
	# Poll interval in seconds, overridable via CIPP_UPDATE_INTERVAL. A missing,
	# non-numeric, or non-positive value falls back to the default.
	raw = os.environ.get("CIPP_UPDATE_INTERVAL")
	if raw is None:
		return DEFAULT_UPDATE_INTERVAL
	try:
		value = int(raw)
	except ValueError:
		return DEFAULT_UPDATE_INTERVAL
	return value if value > 0 else DEFAULT_UPDATE_INTERVAL


def main():
	parser = argparse.ArgumentParser(prog="cipp")
	parser.add_argument("--tab", choices=TAB_NAMES, default="prod")
	args = parser.parse_args()
	present = [(name, tab) for name, tab, on in all_tabs() if on]
	names = [name for name, _ in present]
	tabs = [tab for _, tab in present]
	if args.tab not in names:
		parser.error(f"--tab {args.tab} unavailable (apps/dbs need shifterctl, claude needs ~/.claude/sessions)")
	initial_tab = names.index(args.tab)
	result = run_pr_menu(
		tabs,
		poll_seconds=update_interval(),
		initial_tab=initial_tab,
		updater=update.updater(),
		version=update.version(),
	)
	if isinstance(result, tuple) and result[0] == RESTART:
		# Re-exec into the freshly installed code, back on the same tab (last
		# --tab wins).
		argv = [sys.executable, "-m", "cipp.menu", *sys.argv[1:], "--tab", names[result[1]]]
		os.execv(sys.executable, argv)


if __name__ == "__main__":
	main()
