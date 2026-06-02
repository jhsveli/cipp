SHORTENABLE = {"kundefront", "api", "bm", "pm", "ail", "actions"}


def short_repo(name: str) -> str:
	return "-".join(s[0] if s in SHORTENABLE else s for s in name.split("-"))
