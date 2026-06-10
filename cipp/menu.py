import argparse

from . import mine, prod, prs
from .pr_menu import run_pr_menu


def main():
	parser = argparse.ArgumentParser(prog="cipp")
	parser.add_argument("--tab", choices=["prod", "reviews", "mine"], default="prod")
	args = parser.parse_args()
	initial_tab = {"mine": 0, "reviews": 1, "prod": 2}[args.tab]
	run_pr_menu([mine.TAB, prs.TAB, prod.TAB], initial_tab=initial_tab)


if __name__ == "__main__":
	main()
