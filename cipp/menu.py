import argparse
import os

from . import apps, mine, prod, prs
from .pr_menu import run_pr_menu

DEFAULT_UPDATE_INTERVAL = 30


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
	parser.add_argument("--tab", choices=["prod", "reviews", "mine", "apps"], default="prod")
	args = parser.parse_args()
	tabs = [mine.TAB, prs.TAB, prod.TAB]
	# Apps tab needs shifterctl; without it the tab is hidden.
	if apps.enabled():
		tabs.append(apps.TAB)
	initial_tab = {"mine": 0, "reviews": 1, "prod": 2, "apps": 3}[args.tab]
	if initial_tab >= len(tabs):
		parser.error("--tab apps requires shifterctl on PATH")
	run_pr_menu(
		tabs,
		poll_seconds=update_interval(),
		initial_tab=initial_tab,
	)


if __name__ == "__main__":
	main()
