import argparse

import prod
import prs
from pr_menu import run_pr_menu


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--tab", choices=["prod", "reviews"], default="prod")
	args = parser.parse_args()
	initial_tab = 0 if args.tab == "prod" else 1
	run_pr_menu([prod.TAB, prs.TAB], initial_tab=initial_tab)


if __name__ == "__main__":
	main()
