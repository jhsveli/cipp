import json
import os
import re
import shutil
import signal
import subprocess
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from rich.text import Text

from .pr_menu import ActionResult, ActionSpec, ColumnSpec, TabConfig, format_age

POLL_SECONDS = 60
TIMEOUT = 30  # per shifterctl call
LOGIN_TIMEOUT = 180  # once a login prompt shows: time allowed to confirm in browser
STAGE_ORDER = {"prod": 0, "test": 1}

# PromQL per metric; `$NS` / `$DST` expand to a namespace / Istio
# destination-namespace matcher. One query per metric per cluster covers all
# apps (`by (namespace)`; Istio series are relabelled to `namespace`).
# Denominators are `> 0`-filtered and quantiles `>= 0`-filtered: a NaN anywhere
# makes shifterctl fail the whole query ("unsupported value: NaN").
# CPU/memory = the worst pod's usage vs its limits (a single pod OOMs/throttles).
_ISTIO = 'istio_requests_total{$DST,reporter="destination"}'
_ISTIO_5XX = 'istio_requests_total{$DST,reporter="destination",response_code=~"5.."}'
QUERIES = {
	"ready": 'sum by (namespace) (kube_deployment_status_replicas_available{$NS})',
	"desired": 'sum by (namespace) (kube_deployment_spec_replicas{$NS})',
	"restarts": 'sum by (namespace) (increase(kube_pod_container_status_restarts_total{$NS}[1h]))',
	"cpu": 'max by (namespace) ('
		'sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{$NS,container!="",container!="POD"}[5m]))'
		' / (sum by (namespace, pod) (kube_pod_container_resource_limits{$NS,resource="cpu"}) > 0))',
	"mem": 'max by (namespace) ('
		'sum by (namespace, pod) (container_memory_working_set_bytes{$NS,container!="",container!="POD"})'
		' / (sum by (namespace, pod) (kube_pod_container_resource_limits{$NS,resource="memory"}) > 0))',
	# Termination reasons of containers that restarted within the hour.
	"oom": 'count by (namespace, reason) (kube_pod_container_status_last_terminated_reason{$NS}'
		' and on (namespace, pod, container) (increase(kube_pod_container_status_restarts_total{$NS}[1h]) > 0))',
	"images": 'count by (namespace, image) (kube_pod_container_info{$NS,container="app"})',
	# Unix ts the active ReplicaSet was created = last rollout.
	"deployed": 'max by (namespace) (kube_replicaset_created{$NS}'
		' and on (namespace, replicaset) (kube_replicaset_spec_replicas{$NS} > 0))',
	"rps": 'label_replace(sum by (destination_workload_namespace) (rate(' + _ISTIO + '[5m])),'
		' "namespace", "$1", "destination_workload_namespace", "(.*)")',
	# `or … * 0`: no 5xx series means 0%, not "no data".
	"err": 'label_replace((sum by (destination_workload_namespace) (rate(' + _ISTIO_5XX + '[5m]))'
		' or sum by (destination_workload_namespace) (rate(' + _ISTIO + '[5m])) * 0)'
		' / (sum by (destination_workload_namespace) (rate(' + _ISTIO + '[5m])) > 0),'
		' "namespace", "$1", "destination_workload_namespace", "(.*)")',
	"p95": 'label_replace(histogram_quantile(0.95, sum by (destination_workload_namespace, le)'
		' (rate(istio_request_duration_milliseconds_bucket{$DST,reporter="destination"}[5m]))) >= 0,'
		' "namespace", "$1", "destination_workload_namespace", "(.*)")',
}
# Platform-injected workloads (e.g. support-pgexporter) share the app's
# namespace; excluded so replicas/CPU/image reflect the app only. A negative
# matcher on a label a metric lacks is a no-op, so one set fits all metrics.
_NOT_SUPPORT = ",".join(f'{l}!~"support-.*"' for l in ("deployment", "pod", "replicaset"))

# Metrics whose answer is a label's values (namespace -> sorted list), not a number.
LABEL_METRICS = {"oom": "reason", "images": "image"}

MEM_WARN = 0.9
ERR_WARN = 0.05  # 5xx ratio
ERR_MIN_RPS = 0.01  # ignore the ratio below ~3 errors / 5m (low-traffic noise)


def enabled() -> bool:
	return shutil.which("shifterctl") is not None


class LoginRequired(RuntimeError):
	pass


# Any shifterctl call on an expired session starts a device-flow login inline
# (prints code + URL, polls until confirmed in browser). Output before the JSON
# body is scanned for these to spot it.
LOGIN_MARKERS = ("session expired", "logging in", "confirm sign on", "user code")

_login_failed = False  # last login attempt failed/refused; shows `l` Log in
_login_requested = False  # `l` pressed: next fetch logs in + opens the browser


def login_failed() -> bool:
	return _login_failed


def request_login(_row) -> ActionResult:
	global _login_requested
	_login_requested = True
	return ActionResult.REFRESH


def shifterctl_json(args: list[str], allow_login=False, open_browser=False, notify=lambda text: None):
	# allow_login=False: kill the call on a login prompt (-> LoginRequired), so
	# parallel calls never start competing logins. allow_login=True: let the
	# login run (up to LOGIN_TIMEOUT), surfacing the code via notify.
	proc = subprocess.Popen(
		["shifterctl", *args],
		stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
		text=True, encoding="utf-8",
		start_new_session=True,  # own process group, so kill() reaps children holding the pipe
	)
	killed = threading.Event()

	def kill():
		killed.set()
		try:
			os.killpg(proc.pid, signal.SIGKILL)
		except ProcessLookupError:
			pass

	timer = threading.Timer(TIMEOUT, kill)
	timer.start()
	lines: list[str] = []
	in_json = prompted = announced = False
	url = code = None
	try:
		for line in proc.stdout:
			lines.append(line)
			in_json = in_json or line.startswith("{")
			if in_json:
				continue
			if not prompted and any(m in line.lower() for m in LOGIN_MARKERS):
				prompted = True
				if not allow_login:
					kill()
					break
				timer.cancel()
				timer = threading.Timer(LOGIN_TIMEOUT, kill)
				timer.start()
			if prompted:
				url = url or next(iter(re.findall(r"https?://\S+", line)), None)
				code = code or next(iter(re.findall(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", line)), None)
				if url and code and not announced:
					announced = True
					notify(f"🔑 shifterctl login — confirm {code} in browser")
					if open_browser:
						webbrowser.open(url)
		proc.wait()
	finally:
		timer.cancel()
	out = "".join(lines)
	if prompted and not allow_login:
		raise LoginRequired("shifterctl login expired — press l to log in")
	if prompted and proc.returncode != 0:
		raise LoginRequired("shifterctl login failed — press l to retry")
	if killed.is_set():
		raise RuntimeError(f"shifterctl timed out after {TIMEOUT}s")
	if proc.returncode != 0:
		raise RuntimeError(out.strip()[-300:] or f"shifterctl exited {proc.returncode}")
	if prompted:
		notify("✅ shifterctl logged in")
	start = re.search(r"^\{", out, re.M)
	if start is None:
		raise RuntimeError(f"no JSON from shifterctl: {out.strip()[-300:]}")
	return json.loads(out[start.start():])


_deployments: list[dict] | None = None  # cached once found; team apps rarely change


def deployments(**login) -> list[dict]:
	# One entry per (app, cluster) from the configrepo, for my team's apps.
	global _deployments
	if _deployments is None:
		apps = shifterctl_json(["app", "list", "-o", "json"], **login)["apps"]
		_deployments = [
			{"app": a["name"], "cluster": c["clusterID"], "stage": c["stage"], "namespace": c["namespace"]}
			for a in apps
			for c in a.get("clusters") or []
		]
	return _deployments


def query(cluster: str, metric: str, promql: str, **login) -> dict:
	res = shifterctl_json(["prom", "query", "--cluster", cluster, "-o", "json", promql], **login)
	series = res.get("Vector") or []
	label = LABEL_METRICS.get(metric)
	if label is None:
		return {s["Labels"]["namespace"]: float(s["Value"]) for s in series}
	out: dict[str, list[str]] = {}
	for s in series:
		out.setdefault(s["Labels"]["namespace"], []).append(s["Labels"][label])
	return {ns: sorted(vs) for ns, vs in out.items()}


def fetch(notify) -> list[dict]:
	global _login_failed, _login_requested
	# Auto-login unless a login already failed; then only `l` retries it (and
	# opens the browser). Otherwise each refresh would start a new device flow.
	login = {
		"allow_login": not _login_failed or _login_requested,
		"open_browser": _login_requested,
		"notify": notify,
	}
	_login_requested = False
	try:
		rows = _fetch(login)
	except LoginRequired:
		_login_failed = True
		raise
	_login_failed = False
	return rows


def _fetch(login: dict) -> list[dict]:
	deps = deployments(**login)
	by_cluster: dict[str, list[str]] = {}
	for d in deps:
		by_cluster.setdefault(d["cluster"], []).append(d["namespace"])
	def expand(promql, nss):
		regex = "|".join(nss)
		return (promql
			.replace("$NS", f'namespace=~"{regex}",{_NOT_SUPPORT}')
			.replace("$DST", f'destination_workload_namespace=~"{regex}"'))

	jobs = {
		(cluster, metric): expand(promql, nss)
		for cluster, nss in by_cluster.items()
		for metric, promql in QUERIES.items()
	}
	# First query runs alone as the login gate; the rest fan out on its session.
	(first, first_q), *rest = jobs.items()
	results = {first: query(*first, first_q, **login)}
	with ThreadPoolExecutor(max_workers=len(rest) or 1) as pool:
		futures = {k: pool.submit(query, *k, q) for k, q in rest}
		results |= {k: f.result() for k, f in futures.items()}
	rows = [
		{
			**d,
			"id": f"{d['app']}@{d['cluster']}",
			**{m: results[(d["cluster"], m)].get(d["namespace"]) for m in QUERIES},
		}
		for d in deps
	]
	return sorted(rows, key=lambda r: (STAGE_ORDER.get(r["stage"], 9), r["app"]))


def health(row) -> str:
	ready, desired = row["ready"], row["desired"]
	if desired is None:
		return "· no data"
	if desired == 0:
		return "· scaled to 0"
	if not ready:
		return "✗ down"
	if ready < desired:
		return "⚠ degraded"
	if "OOMKilled" in (row["oom"] or []):
		return "⚠ OOMKilled"
	if row["restarts"] and round(row["restarts"]) >= 1:
		return "⚠ restarting"
	if row["err"] and row["err"] >= ERR_WARN and row["err"] * (row["rps"] or 0) >= ERR_MIN_RPS:
		return "⚠ 5xx"
	if row["mem"] is not None and row["mem"] >= MEM_WARN:
		return "⚠ memory"
	return "✓ healthy"


# Concern colour by health-label prefix; the offending column per label.
CONCERN_COLOR = {"✗": "bold red", "⚠": "bold yellow"}
CONCERN_COLUMN = {
	"down": "ready", "degraded": "ready",
	"OOMKilled": "restarts", "restarting": "restarts",
	"5xx": "err", "memory": "mem",
}


def concern(row) -> tuple[str, str | None] | None:
	# (colour, offending column key) when the app's health is a concern, else None.
	label = health(row)
	color = CONCERN_COLOR.get(label[0])
	if color is None:
		return None
	return color, CONCERN_COLUMN.get(label[2:])


def highlight(key: str, render):
	# Column render that paints the cell in the concern colour when it's the
	# offending metric.
	def styled(row):
		c = concern(row)
		return Text(render(row), style=c[0] if c and c[1] == key else "")
	return styled


def concern_styled(render):
	# Render painted in the concern colour whenever the app has one (app name, health).
	def styled(row):
		c = concern(row)
		return Text(render(row), style=c[0] if c else "")
	return styled


def title_badge(rows, fetch_failed) -> Text:
	# ✅ all fine, ⚠ any prod concern — red if any prod app is down, else yellow;
	# a failed fetch is yellow ⚠ (stale rows can't vouch for health). Prod fine,
	# test concern: ✅ (Test ⚠︎), coloured by worst test concern. Nothing before
	# the first load.
	prod = {health(r)[0] for r in rows if r["stage"] != "test"}
	test = {health(r)[0] for r in rows if r["stage"] == "test"}
	if "✗" in prod:
		return Text("⚠", style=CONCERN_COLOR["✗"])
	if fetch_failed or "⚠" in prod:
		return Text("⚠", style=CONCERN_COLOR["⚠"])
	if not rows:
		return Text("")
	for p in ("✗", "⚠"):
		if p in test:
			return Text("✅ ").append("(Test ⚠︎)", style=CONCERN_COLOR[p])
	return Text("✅")


def pct(v) -> str:
	if v is None:
		return "–"
	# One decimal for small non-zero ratios, so a 0.3% error rate isn't "0%".
	return f"{v * 100:.1f}%" if 0 < v < 0.1 else f"{v * 100:.0f}%"


def rps(row) -> str:
	v = row["rps"]
	if v is None:
		return "–"
	if v == 0:
		return "0/s"
	return f"{v:.2f}/s" if v < 0.1 else f"{v:.1f}/s" if v < 10 else f"{v:.0f}/s"


def p95(row) -> str:
	v = row["p95"]
	if v is None:
		return "–"
	return f"{v:.0f}ms" if v < 1000 else f"{v / 1000:.1f}s"


def deployed(row) -> str:
	ts = row["deployed"]
	if ts is None:
		return "–"
	return format_age(datetime.fromtimestamp(ts, timezone.utc).isoformat())


def tags(row) -> list[str]:
	return [img.rsplit(":", 1)[-1] for img in row["images"] or []]


def replicas(row) -> str:
	if row["desired"] is None:
		return "–"
	return f"{int(row['ready'] or 0)}/{int(row['desired'])}"


def restarts(row) -> str:
	return "–" if row["restarts"] is None else str(round(row["restarts"]))


def preview(row) -> str:
	images = ", ".join(tags(row)) or "–"
	if len(tags(row)) > 1:
		images += " (rollout in progress)"
	return f"""**{row['app']}** · {row['stage']} (`{row['cluster']}`, ns `{row['namespace']}`) · {health(row)}

- **Replicas** {replicas(row)} ready · **restarts (1h)** {restarts(row)} · **reasons** {', '.join(row['oom'] or []) or '–'}
- **CPU** {pct(row['cpu'])} · **memory** {pct(row['mem'])} (worst pod vs limit)
- **Traffic (5m)** {rps(row)} · **5xx** {pct(row['err'])} · **p95** {p95(row)}
- **Deployed** {deployed(row)} ago · **image** {images}
"""


TAB = TabConfig(
	name="Apps",
	title="Team app status (Prometheus via shifterctl)",
	fetch=fetch,
	poll_seconds=POLL_SECONDS,
	noun="app",
	actions=[
		ActionSpec(
			key="l",
			label="Log in",
			handler=request_login,
			tab_level=True,
			visible=login_failed,
		),
	],
	status_bar=preview,
	pr_label=concern_styled(lambda row: row["app"]),
	idle_label=concern_styled(health),
	# Flag the inactive tab title only when an app's health changes, not on
	# every metric wiggle.
	state_signature=health,
	title_badge=title_badge,
	show_header=True,
	status_label="Health",
	preview_title="Details",
	pane_ratio=(3, 2),
	columns=[
		ColumnSpec(key="stage", label="Env", width=4, render=lambda r: r["stage"]),
		ColumnSpec(key="ready", label="Ready", width=5, render=highlight("ready", replicas)),
		ColumnSpec(key="restarts", label="Restarts", width=8, render=highlight("restarts", restarts)),
		ColumnSpec(key="cpu", label="CPU", width=4, render=lambda r: pct(r["cpu"])),
		ColumnSpec(key="mem", label="Mem", width=4, render=highlight("mem", lambda r: pct(r["mem"]))),
		ColumnSpec(key="rps", label="Req/s", width=6, render=rps),
		ColumnSpec(key="err", label="5xx", width=5, render=highlight("err", lambda r: pct(r["err"]))),
		ColumnSpec(key="p95", label="p95", width=6, render=p95),
		ColumnSpec(key="deployed", label="Deployed", width=8, render=deployed),
	],
)
