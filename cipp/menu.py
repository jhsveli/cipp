import argparse
import os
import sys

from . import apps, mine, prod, prs, update
from .pr_menu import RESTART, run_pr_menu

DEFAULT_UPDATE_INTERVAL = 30
TAB_NAMES = ["mine", "reviews", "prod", "apps"]  # --tab values, in tab order


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
	tabs = [mine.TAB, prs.TAB, prod.TAB]
	# Apps tab needs shifterctl; without it the tab is hidden.
	if apps.enabled():
		tabs.append(apps.TAB)
	initial_tab = TAB_NAMES.index(args.tab)
	if initial_tab >= len(tabs):
		parser.error("--tab apps requires shifterctl on PATH")
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
		argv = [sys.executable, "-m", "cipp.menu", *sys.argv[1:], "--tab", TAB_NAMES[result[1]]]
		os.execv(sys.executable, argv)


if __name__ == "__main__":
	main()
