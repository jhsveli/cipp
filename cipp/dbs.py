from . import apps
from .apps import STAGE_ORDER, Styling, pct
from .pr_menu import ActionSpec, ColumnSpec, TabConfig

POLL_SECONDS = 30

# Two sources, both labelled with the app's namespace: postgres_exporter
# (`support-pgexporter` in the app ns; >1 replica -> `max by` over pods) and
# YACE CloudWatch (`aws_rds_*`). All `by (namespace)`: assumes one DB instance
# per app namespace (true for all team apps). `$NS` = namespace matcher, `$DB` =
# app databases only (not postgres/rdsadmin/templates). Fetched as one `bulk`
# query per cluster; denominators `> 0`-filtered (a NaN fails the whole query).
_DB = 'datname!~"postgres|rdsadmin|template.*"'
_HIT = 'rate(pg_stat_database_blks_hit{$NS,$DB}[5m])'
_READ = 'rate(pg_stat_database_blks_read{$NS,$DB}[5m])'
QUERIES = {
	"up": 'max by (namespace) (pg_up{$NS})',
	"conns": 'max by (namespace) (sum by (namespace, pod) (pg_stat_activity_count{$NS}))',
	"max_conns": 'max by (namespace) (pg_settings_max_connections{$NS})',
	"idle_tx": 'max by (namespace) (sum by (namespace, pod) (pg_stat_activity_count{$NS,state="idle in transaction"}))',
	"long_tx": 'max by (namespace) (pg_stat_activity_max_tx_duration{$NS})',
	"tps": 'max by (namespace) (sum by (namespace, pod) (rate(pg_stat_database_xact_commit{$NS,$DB}[5m])'
		' + rate(pg_stat_database_xact_rollback{$NS,$DB}[5m])))',
	"cache": f'max by (namespace) (sum by (namespace, pod) ({_HIT})'
		f' / (sum by (namespace, pod) ({_HIT} + {_READ}) > 0))',
	"deadlocks": 'max by (namespace) (sum by (namespace, pod) (increase(pg_stat_database_deadlocks{$NS,$DB}[1h])))',
	# Exporter emits size with and without instance labels: dedupe per datname.
	"size": 'sum by (namespace) (max by (namespace, datname) (pg_database_size_bytes{$NS,$DB}))',
	"dead_tup": 'sum by (namespace) (max by (namespace, datname, schemaname, relname) (pg_stat_user_tables_n_dead_tup{$NS}))',
	"cpu": 'max by (namespace) (aws_rds_cpuutilization_maximum{$NS}) / 100',
	"free_mem": 'max by (namespace) (aws_rds_freeable_memory_minimum{$NS})',
	"swap": 'max by (namespace) (aws_rds_swap_usage_maximum{$NS})',
	# Allocated storage isn't exported, so only absolute free space.
	"free_disk": 'max by (namespace) (aws_rds_free_storage_space_minimum{$NS})',
	"queue": 'max by (namespace) (aws_rds_disk_queue_depth_maximum{$NS})',
	"read_iops": 'max by (namespace) (aws_rds_read_iops_maximum{$NS})',
	"write_iops": 'max by (namespace) (aws_rds_write_iops_maximum{$NS})',
	"xid": 'max by (namespace) (aws_rds_maximum_used_transaction_ids_maximum{$NS})',
	"credits": 'max by (namespace) (aws_rds_cpucredit_balance_sum{$NS})',
}
# Instance id label value (namespace -> RDS instance id), for the details pane.
INSTANCE_QUERY = 'count by (namespace, dimension_DBInstanceIdentifier) (aws_rds_cpuutilization_maximum{$NS})'

CONNS_WARN = 0.8  # of max_connections
CPU_WARN = 0.9
DISK_WARN = 5e9  # bytes free
LONG_TX_WARN = 300  # s
XID_WARN = 1e9  # of ~2.1e9 before wraparound


def _fetch(login: dict) -> list[dict]:
	deps, by_cluster = apps.gated_deployments(login)

	def expand(promql, nss):
		return promql.replace("$DB", _DB).replace("$NS", f'namespace=~"{"|".join(nss)}"')

	jobs = {
		cluster: (lambda c=cluster, q=expand(apps.bulk(QUERIES), nss), **kw: apps.query_bulk(c, q, **kw))
		for cluster, nss in by_cluster.items()
	}
	jobs |= {
		(cluster, "instance"): (lambda c=cluster, q=expand(INSTANCE_QUERY, nss), **kw: {
			s["Labels"]["namespace"]: s["Labels"]["dimension_DBInstanceIdentifier"]
			for s in apps._series(c, q, **kw)
		})
		for cluster, nss in by_cluster.items()
	}
	results = apps.run_jobs(jobs, login)
	rows = []
	for d in deps:
		metrics = {m: results[d["cluster"]].get(m, {}).get(d["namespace"]) for m in QUERIES}
		instance = results[(d["cluster"], "instance")].get(d["namespace"])
		# Apps without a DB have no series at all: not a row.
		if instance is None and all(v is None for v in metrics.values()):
			continue
		rows.append({**d, "id": f"{d['app']}@{d['cluster']}", "instance": instance, **metrics})
	apps.db_health = {r["id"]: health(r) for r in rows}
	return sorted(rows, key=lambda r: (STAGE_ORDER.get(r["stage"], 9), r["app"]))


fetch = apps.login_fetch(_fetch)


def conns_ratio(row) -> float | None:
	if row["conns"] is None or not row["max_conns"]:
		return None
	return row["conns"] / row["max_conns"]


def health(row) -> str:
	if row["up"] is None and row["cpu"] is None:
		return "· no data"
	if row["up"] == 0:
		return "✗ down"
	if (conns_ratio(row) or 0) >= CONNS_WARN:
		return "⚠ conns"
	if row["free_disk"] is not None and row["free_disk"] < DISK_WARN:
		return "⚠ disk"
	if (row["cpu"] or 0) >= CPU_WARN:
		return "⚠ cpu"
	if (row["long_tx"] or 0) >= LONG_TX_WARN:
		return "⚠ long tx"
	if row["deadlocks"] and round(row["deadlocks"]) >= 1:
		return "⚠ deadlocks"
	if (row["xid"] or 0) >= XID_WARN:
		return "⚠ xid"
	return "✓ healthy"


STYLING = Styling(health, {"down": "conns", "conns": "conns", "disk": "disk", "cpu": "cpu", "long tx": "tx"})


def size(v) -> str:
	if v is None:
		return "–"
	for unit, scale in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("k", 1e3)):
		if v >= scale:
			n = v / scale
			return f"{n:.1f}{unit}" if n < 10 else f"{n:.0f}{unit}"
	return f"{v:.0f}"


def num(v, suffix="") -> str:
	if v is None:
		return "–"
	return f"{v:.1f}{suffix}" if v < 10 else f"{v:.0f}{suffix}"


def count(v) -> str:
	return "–" if v is None else str(round(v))


def conns(row) -> str:
	if row["conns"] is None:
		return "–"
	return f"{int(row['conns'])}/{int(row['max_conns'])}" if row["max_conns"] else str(int(row["conns"]))


def seconds(v) -> str:
	if v is None:
		return "–"
	return f"{v:.0f}s" if v < 60 else f"{v / 60:.0f}m" if v < 3600 else f"{v / 3600:.1f}h"


def preview(row) -> str:
	ratio = conns_ratio(row)
	return f"""**{row['app']}** · {row['stage']} (`{row['cluster']}`, ns `{row['namespace']}`) · {health(row)}

- **Instance** `{row['instance'] or '–'}` · **exporter** {'up' if row['up'] else 'down' if row['up'] == 0 else '–'}
- **Connections** {conns(row)} ({pct(ratio)}) · **idle in tx** {count(row['idle_tx'])} · **longest tx** {seconds(row['long_tx'])}
- **TPS (5m)** {num(row['tps'])} · **cache hit** {pct(row['cache'])} · **deadlocks (1h)** {count(row['deadlocks'])}
- **CPU** {pct(row['cpu'])} · **freeable mem** {size(row['free_mem'])}B · **swap** {size(row['swap'])}B · **CPU credits** {num(row['credits'])}
- **Free disk** {size(row['free_disk'])}B · **DB size** {size(row['size'])}B · **dead tuples** {size(row['dead_tup'])}
- **IOPS** read {num(row['read_iops'])} · write {num(row['write_iops'])} · **disk queue** {num(row['queue'])} · **max used XIDs** {size(row['xid'])}
"""


h = STYLING.highlight

TAB = TabConfig(
	name="DBs",
	title="Team database status (Prometheus via shifterctl)",
	fetch=fetch,
	poll_seconds=POLL_SECONDS,
	noun="database",
	actions=[
		ActionSpec(
			key="l",
			label="Log in",
			handler=apps.request_login,
			tab_level=True,
			visible=apps.login_failed,
		),
		ActionSpec(
			key="s",
			label="Open in Shifter",
			handler=apps.opener(""),
			breadcrumb=lambda r: f"Opened {r['app']} in Shifter",
		),
		ActionSpec(
			key="g",
			label="Open in Grafana",
			handler=apps.opener("/grafana"),
			breadcrumb=lambda r: f"Opened {r['app']} Grafana in Shifter",
		),
	],
	status_bar=preview,
	pr_label=STYLING.concern_styled(lambda row: row["app"]),
	idle_label=STYLING.concern_styled(health),
	state_signature=health,
	title_badge=STYLING.title_badge,
	show_header=True,
	status_label="Health",
	preview_title="Details",
	pane_ratio=(3, 2),
	columns=[
		ColumnSpec(key="stage", label="Env", width=4, render=h("stage", lambda r: r["stage"])),
		ColumnSpec(key="conns", label="Conns", width=9, render=h("conns", conns)),
		ColumnSpec(key="cpu", label="CPU", width=4, render=h("cpu", lambda r: pct(r["cpu"]))),
		ColumnSpec(key="mem", label="FreeMem", width=7, render=h("mem", lambda r: size(r["free_mem"]))),
		ColumnSpec(key="disk", label="FreeDisk", width=8, render=h("disk", lambda r: size(r["free_disk"]))),
		ColumnSpec(key="size", label="Size", width=5, render=h("size", lambda r: size(r["size"]))),
		ColumnSpec(key="tps", label="TPS", width=4, render=h("tps", lambda r: num(r["tps"]))),
		ColumnSpec(key="cache", label="Cache", width=5, render=h("cache", lambda r: pct(r["cache"]))),
		ColumnSpec(key="tx", label="LongTx", width=6, render=h("tx", lambda r: seconds(r["long_tx"]))),
		ColumnSpec(key="queue", label="Queue", width=5, render=h("queue", lambda r: num(r["queue"]))),
	],
)
