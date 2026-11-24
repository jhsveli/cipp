import glob
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone

from rich.text import Text

from .cmd import exec, exec_input
from .pr_menu import ActionResult, ActionSpec, ColumnSpec, TabConfig, age_key, format_age, spinner

CLAUDE_DIR = os.path.expanduser("~/.claude")
SESSIONS_GLOB = os.path.join(CLAUDE_DIR, "sessions", "*.json")
HISTORY = os.path.join(CLAUDE_DIR, "history.jsonl")
HISTORY_TAIL_BYTES = 2 * 1024 * 1024
POLL_SECONDS = 5
WAIT_COLOR = "bold yellow"
HOME = os.path.expanduser("~")


def enabled() -> bool:
	return os.path.isdir(os.path.join(CLAUDE_DIR, "sessions"))


def iso(ms) -> str | None:
	return None if not ms else datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def process_starts(pids: list[int]) -> dict[int, float]:
	# pid -> process start (epoch s), live pids only. One ps call.
	if not pids:
		return {}
	out = subprocess.run(
		["ps", "-p", ",".join(map(str, pids)), "-o", "pid=,lstart="],
		capture_output=True, text=True,
	).stdout
	starts = {}
	for line in out.splitlines():
		pid, _, lstart = line.strip().partition(" ")
		try:
			starts[int(pid)] = datetime.strptime(" ".join(lstart.split()), "%a %b %d %H:%M:%S %Y").timestamp()
		except ValueError:
			continue
	return starts


def last_prompts() -> dict[str, int]:
	# sessionId -> ms of my last typed prompt, from history.jsonl's tail.
	try:
		with open(HISTORY, "rb") as f:
			f.seek(max(0, os.path.getsize(HISTORY) - HISTORY_TAIL_BYTES))
			lines = f.read().decode("utf-8", "replace").splitlines()[1:]  # 1st may be partial
	except OSError:
		return {}
	last = {}
	for line in lines:
		try:
			e = json.loads(line)
		except ValueError:
			continue
		sid, ts = e.get("sessionId"), e.get("timestamp")
		if sid and ts and ts > last.get(sid, 0):
			last[sid] = ts
	return last


def fetch(notify) -> list[dict]:
	sessions = []
	for path in glob.glob(SESSIONS_GLOB):
		try:
			with open(path) as f:
				sessions.append(json.load(f))
		except (OSError, ValueError):
			continue
	starts = process_starts([s["pid"] for s in sessions if s.get("pid")])
	prompts = last_prompts()
	rows = []
	for s in sessions:
		# Stale registry file: pid gone, or reused by a process started after
		# the session (procStart's timezone differs from ps, so compare epochs).
		start = starts.get(s.get("pid"))
		if start is None or (s.get("startedAt") and start > s["startedAt"] / 1000 + 5):
			continue
		cwd = s.get("cwd") or ""
		rows.append({
			# One session can run in several processes (resumed in another terminal).
			"id": f"{s['sessionId']}/{s['pid']}",
			"sessionId": s["sessionId"],
			"pid": s["pid"],
			"name": s.get("name") or s["sessionId"][:8],
			"cwd": cwd,
			"dir": "~" + cwd[len(HOME):] if cwd.startswith(HOME) else cwd,
			"kind": s.get("kind") or "interactive",
			"status": s.get("status") or "idle",
			"waitingFor": s.get("waitingFor"),
			"jobId": s.get("jobId"),
			"version": s.get("version"),
			"startedAt": iso(s.get("startedAt")),
			"statusAt": iso(s.get("statusUpdatedAt") or s.get("updatedAt")),
			"promptAt": iso(prompts.get(s["sessionId"])),
		})
	# Waiting first (needs me), then most recently prompted.
	rows.sort(key=lambda r: (r["status"] != "waiting", -(datetime.fromisoformat(r["promptAt"]).timestamp() if r["promptAt"] else 0)))
	return rows


def is_bg(row) -> bool:
	return row["kind"] == "bg" and bool(row["jobId"])


def age(field):
	return lambda r: format_age(r[field]) if r[field] else "–"


def age_sort(field):
	return lambda r: age_key(r[field]) if r[field] else None


def status_label(row):
	if row["status"] == "waiting":
		return Text(f"⚠ {row['waitingFor'] or 'waiting'}", style=WAIT_COLOR)
	if row["status"] == "busy":
		return f"{spinner()} busy"
	return row["status"]


def attach(row):
	# cipp owns its terminal → attach in a new iTerm window.
	cmd = f"/bin/zsh -lc {shlex.quote('claude attach ' + shlex.quote(row['jobId']))}"
	script = f'tell application "iTerm" to create window with default profile command {json.dumps(cmd)}'
	exec(["osascript", "-e", script])
	return ActionResult.KEEP


def resume_cmd(row) -> str:
	return f"cd {shlex.quote(row['cwd'])} && claude --resume {row['sessionId']}"


def copy_resume(row):
	exec_input(["pbcopy"], resume_cmd(row))
	return ActionResult.KEEP


def title_badge(rows, fetch_failed):
	waiting = sum(r["status"] == "waiting" for r in rows)
	if waiting:
		return Text(f"⚠ {waiting}", style=WAIT_COLOR)
	return f"({len(rows)})"


def preview(row) -> str:
	since = format_age(row["statusAt"]) if row["statusAt"] else "–"
	prompt = format_age(row["promptAt"]) + " ago" if row["promptAt"] else "–"
	return f"""**{row['name']}** · {row['kind']} · {row['status']}{f" ({row['waitingFor']})" if row['waitingFor'] else ""} for {since}

- **Dir** `{row['cwd']}`
- **Session** `{row['sessionId']}`{f" · **job** `{row['jobId']}`" if row['jobId'] else ""} · **pid** {row['pid']} · **version** {row['version'] or '–'}
- **Started** {format_age(row['startedAt']) + ' ago' if row['startedAt'] else '–'} · **last prompt** {prompt}
- **Resume** `{resume_cmd(row)}`
"""


TAB = TabConfig(
	name="Claude",
	title="Local Claude Code sessions",
	fetch=fetch,
	poll_seconds=POLL_SECONDS,
	noun="session",
	actions=[
		ActionSpec(
			key="a",
			label="Attach",
			handler=attach,
			block=lambda r: None if is_bg(r) else "Only background sessions can be attached",
			breadcrumb=lambda r: f"Attaching {r['name']} in iTerm",
		),
		ActionSpec(
			key="c",
			label="Copy resume cmd",
			handler=copy_resume,
			breadcrumb=lambda r: "Copied resume command",
		),
	],
	status_bar=preview,
	pr_label=lambda r: r["name"],
	idle_label=status_label,
	animated=lambda r: r["status"] == "busy",
	state_signature=lambda r: (r["status"], r["waitingFor"]),
	title_badge=title_badge,
	show_header=True,
	status_label="Status",
	preview_title="Details",
	pane_ratio=(3, 2),
	columns=[
		ColumnSpec(key="kind", label="Kind", width=4, render=lambda r: "bg" if r["kind"] == "bg" else "tty"),
		ColumnSpec(key="dir", label="Dir", width=36, render=lambda r: r["dir"]),
		ColumnSpec(key="prompt", label="Prompt", width=7, render=age("promptAt"), sort_key=age_sort("promptAt")),
		ColumnSpec(key="since", label="Since", width=6, render=age("statusAt"), sort_key=age_sort("statusAt")),
	],
)
