from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Header, Static, TabbedContent, TabPane


class ActionResult(Enum):
	REMOVE = "remove"
	KEEP = "keep"


@dataclass
class ActionSpec:
	key: str
	label: str
	handler: Callable[[dict], ActionResult]


@dataclass
class ColumnSpec:
	key: str
	label: str
	width: int | None
	render: Callable[[dict], Any]


@dataclass
class TabConfig:
	name: str
	title: str
	fetch: Callable[[], list[dict]]
	actions: list[ActionSpec]
	status_bar: Callable[[dict], str] = lambda pr: ""
	columns: list[ColumnSpec] = field(default_factory=list)
	pr_label: Callable[[dict], str] = lambda pr: f"{pr['number']:>6} {pr['title']}"


@dataclass
class TabState:
	config: TabConfig
	table_id: str
	actions_by_key: dict[str, ActionSpec]
	prs: list[dict] = field(default_factory=list)
	removed_ids: set[str] = field(default_factory=set)
	acting_pr_ids: dict[str, str] = field(default_factory=dict)
	finishing_pr_ids: set[str] = field(default_factory=set)
	seconds_until_refresh: int = 0


class PRMenuApp(App):
	CSS = """
	Screen { layout: vertical; }
	#statusbar { height: 3; border: round $success; padding: 0 1; }
	#statusbar-row { height: 1; }
	#tabs { height: 2fr; }
	#status { height: 3fr; padding: 0 1; color: $text-muted; overflow-y: auto; border: round gray; border-title-color: gray; }
	#countdown { width: auto; padding: 0 1; color: white; text-style: italic; }
	#countdown.running { color: $success; }
	#breadcrumb { width: 1fr; padding: 0 1; color: white; text-align: right; }
	#breadcrumb.running { color: $success; }
	.hotkeys { height: 1; padding: 0 1; color: $text; }
	DataTable { height: 1fr; }
	"""

	BINDINGS = [
		Binding("q", "quit", "Quit"),
		Binding("escape", "quit", "Quit"),
		Binding("left", "previous_tab", "Prev tab", priority=True),
		Binding("right", "next_tab", "Next tab", priority=True),
	]

	SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

	def __init__(
		self,
		tabs: list[TabConfig],
		poll_seconds: int,
		initial_tab: int,
	):
		super().__init__()
		self._poll_seconds = poll_seconds
		self._initial_tab = initial_tab
		self._loading_tabs: set[int] = set()
		self._spinner_frame = 0
		self._tabs: list[TabState] = [
			TabState(
				config=cfg,
				table_id=f"table-{i}",
				actions_by_key={a.key: a for a in cfg.actions},
				seconds_until_refresh=poll_seconds,
			)
			for i, cfg in enumerate(tabs)
		]

	def compose(self) -> ComposeResult:
		yield Header()
		with TabbedContent(id="tabs", initial=f"tab-{self._initial_tab}"):
			for i, ts in enumerate(self._tabs):
				with TabPane(ts.config.name, id=f"tab-{i}"):
					yield DataTable(
						id=ts.table_id,
						show_header=False,
						cursor_type="row",
						zebra_stripes=False,
					)
					yield Static("", id=f"hotkeys-{i}", classes="hotkeys")
		with Vertical(id="statusbar"):
			with Horizontal(id="statusbar-row"):
				yield Static("", id="countdown")
				yield Static("", id="breadcrumb")
		status = Static("", id="status")
		status.border_title = "Preview"
		yield status

	def on_mount(self) -> None:
		self.title = self._tabs[self._initial_tab].config.title
		for ts in self._tabs:
			table = self.query_one(f"#{ts.table_id}", DataTable)
			for col in ts.config.columns:
				table.add_column(col.label, key=col.key, width=col.width)
			table.add_column("PR", key="pr")
			table.add_column("Status", key="status", width=20)
		for i in range(len(self._tabs)):
			self._refresh_tab(i)
		self.set_interval(1, self._tick_countdown)
		self.set_interval(0.1, self._tick_spinner)
		self._render_countdown()
		self._render_hotkeys()

	def _active_index(self) -> int:
		active = self.query_one(TabbedContent).active
		if active and active.startswith("tab-"):
			return int(active.removeprefix("tab-"))
		return 0

	def _tick_countdown(self) -> None:
		for i, ts in enumerate(self._tabs):
			if i in self._loading_tabs:
				continue
			if ts.acting_pr_ids or ts.finishing_pr_ids:
				continue
			ts.seconds_until_refresh -= 1
			if ts.seconds_until_refresh <= 0:
				self._refresh_tab(i)
		self._render_countdown()

	def _tick_spinner(self) -> None:
		any_acting = any(ts.acting_pr_ids for ts in self._tabs)
		if not self._loading_tabs and not any_acting:
			return
		self._spinner_frame = (self._spinner_frame + 1) % len(self.SPINNER_FRAMES)
		if self._loading_tabs:
			self._render_countdown()
		if any_acting:
			for i, ts in enumerate(self._tabs):
				for pr_id in list(ts.acting_pr_ids.keys()):
					self._refresh_row(i, pr_id)

	def _render_countdown(self) -> None:
		i = self._active_index()
		ts = self._tabs[i]
		count = f"{len(ts.prs)} PR(s) · "
		running = i in self._loading_tabs
		if running:
			text = f"{count}{self.SPINNER_FRAMES[self._spinner_frame]} Updating"
		else:
			text = f"{count}Updating in {ts.seconds_until_refresh}s…"
		countdown = self.query_one("#countdown", Static)
		countdown.update(text)
		countdown.set_class(running, "running")

	def _left_cell(self, ts: TabState, pr: dict) -> str:
		return ts.config.pr_label(pr)

	def _right_cell(self, ts: TabState, pr_id: str, dim: bool = False) -> Text:
		if pr_id in ts.acting_pr_ids:
			label = ts.acting_pr_ids[pr_id]
			content = f"{self.SPINNER_FRAMES[self._spinner_frame]} {label}"
		elif pr_id in ts.finishing_pr_ids:
			content = "✓ Done"
		else:
			content = ""
		return Text(content, style="dim" if dim else "", justify="right")

	def _refresh_row(self, i: int, pr_id: str, dim: bool = False) -> None:
		ts = self._tabs[i]
		table = self.query_one(f"#{ts.table_id}", DataTable)
		try:
			table.update_cell(pr_id, "status", self._right_cell(ts, pr_id, dim=dim))
		except Exception:
			pass

	def _render_hotkeys(self) -> None:
		key_label = {"enter": "↵", "escape": "esc"}
		for i, ts in enumerate(self._tabs):
			parts = [f"[b]{key_label.get(a.key, a.key)}[/b] {a.label}" for a in ts.config.actions]
			parts.extend(["[b]←/→[/b] Switch tab", "[b]q[/b] Quit"])
			self.query_one(f"#hotkeys-{i}", Static).update("  ".join(parts))

	def _refresh_tab(self, i: int) -> None:
		self.run_worker(
			lambda: self._fetch_tab(i),
			thread=True,
			exclusive=True,
			group=f"fetch-{i}",
		)

	def _fetch_tab(self, i: int) -> None:
		self.call_from_thread(self._reset_countdown, i)
		self.call_from_thread(self._mark_loading, i, True)
		try:
			prs = self._tabs[i].config.fetch()
		except Exception as e:
			self.call_from_thread(self._mark_loading, i, False)
			self.call_from_thread(self._set_breadcrumb, f"fetch failed: {e}", i)
			return
		self.call_from_thread(self._apply_prs, i, prs)

	def _mark_loading(self, i: int, loading: bool) -> None:
		if loading:
			self._loading_tabs.add(i)
		else:
			self._loading_tabs.discard(i)
		if i == self._active_index():
			self._render_countdown()

	def _reset_countdown(self, i: int) -> None:
		self._tabs[i].seconds_until_refresh = self._poll_seconds
		if i == self._active_index():
			self._render_countdown()

	def _apply_prs(self, i: int, prs: list[dict]) -> None:
		ts = self._tabs[i]
		if ts.acting_pr_ids or ts.finishing_pr_ids:
			self._mark_loading(i, False)
			return
		visible = [pr for pr in prs if pr["id"] not in ts.removed_ids]
		old_ids = {pr["id"] for pr in ts.prs}
		new_ids = {pr["id"] for pr in visible}

		if old_ids == new_ids and ts.prs:
			ts.prs = visible
			self._mark_loading(i, False)
			return

		table = self.query_one(f"#{ts.table_id}", DataTable)
		highlighted_id = None
		if ts.prs and table.row_count > 0:
			cursor = table.cursor_row
			if 0 <= cursor < len(ts.prs):
				highlighted_id = ts.prs[cursor]["id"]

		ts.prs = visible
		table.clear()
		for pr in visible:
			cells = [col.render(pr) for col in ts.config.columns]
			cells.append(self._left_cell(ts, pr))
			cells.append(self._right_cell(ts, pr["id"]))
			table.add_row(*cells, key=pr["id"])

		if visible:
			next_index = 0
			if highlighted_id is not None:
				for j, pr in enumerate(visible):
					if pr["id"] == highlighted_id:
						next_index = j
						break
			table.move_cursor(row=next_index)
			if i == self._active_index():
				self._update_status(ts.config.status_bar(visible[next_index]))
		else:
			if i == self._active_index():
				self._update_status("")

		self._mark_loading(i, False)
		added = len(new_ids - old_ids)
		if added and old_ids:
			self._set_breadcrumb(f"+{added} new PR(s)", i)
		if i == self._active_index():
			self._render_countdown()

	def _tab_index_for_table(self, table_id: str | None) -> int | None:
		if not table_id:
			return None
		for i, ts in enumerate(self._tabs):
			if ts.table_id == table_id:
				return i
		return None

	def on_data_table_row_highlighted(
		self, event: DataTable.RowHighlighted
	) -> None:
		i = self._tab_index_for_table(event.data_table.id)
		if i is None or i != self._active_index():
			return
		ts = self._tabs[i]
		if 0 <= event.cursor_row < len(ts.prs):
			self._update_status(ts.config.status_bar(ts.prs[event.cursor_row]))

	def on_data_table_row_selected(
		self, event: DataTable.RowSelected
	) -> None:
		i = self._tab_index_for_table(event.data_table.id)
		if i is None:
			return
		if "enter" in self._tabs[i].actions_by_key:
			self._run_action_on_tab(i, event.cursor_row, "enter")

	def on_tabbed_content_tab_activated(
		self, event: TabbedContent.TabActivated
	) -> None:
		i = self._active_index()
		ts = self._tabs[i]
		self.title = ts.config.title
		table = self.query_one(f"#{ts.table_id}", DataTable)
		if table.row_count > 0 and 0 <= table.cursor_row < len(ts.prs):
			self._update_status(ts.config.status_bar(ts.prs[table.cursor_row]))
		else:
			self._update_status("")
		self._render_countdown()
		self._render_hotkeys()
		self._set_breadcrumb("")
		table.focus()

	def action_previous_tab(self) -> None:
		self._switch_tab(-1)

	def action_next_tab(self) -> None:
		self._switch_tab(1)

	def _switch_tab(self, delta: int) -> None:
		n = len(self._tabs)
		new_index = (self._active_index() + delta) % n
		self.query_one(TabbedContent).active = f"tab-{new_index}"

	def _update_status(self, text: str) -> None:
		self.query_one("#status", Static).update(text)

	def _set_breadcrumb(
		self, text: str, tab_index: int | None = None, running: bool = False
	) -> None:
		if tab_index is not None and tab_index != self._active_index():
			return
		breadcrumb = self.query_one("#breadcrumb", Static)
		breadcrumb.update(text)
		breadcrumb.set_class(running, "running")

	def on_key(self, event) -> None:
		i = self._active_index()
		spec = self._tabs[i].actions_by_key.get(event.key)
		if spec is None or event.key == "enter":
			return
		table = self.query_one(f"#{self._tabs[i].table_id}", DataTable)
		if table.row_count == 0:
			return
		event.stop()
		self._run_action_on_tab(i, table.cursor_row, event.key)

	def _run_action_on_tab(self, i: int, index: int, key: str) -> None:
		ts = self._tabs[i]
		if not (0 <= index < len(ts.prs)):
			return
		spec = ts.actions_by_key.get(key)
		if spec is None:
			return
		pr = ts.prs[index]
		pr_id = pr["id"]
		if pr_id in ts.acting_pr_ids or pr_id in ts.finishing_pr_ids:
			return
		ts.acting_pr_ids[pr_id] = spec.label
		self._refresh_row(i, pr_id)
		self.run_worker(
			lambda: self._invoke(i, spec, pr),
			thread=True,
			exclusive=True,
			group=f"action-{i}-{pr_id}",
		)

	def _invoke(self, i: int, spec: ActionSpec, pr: dict) -> None:
		try:
			result = spec.handler(pr)
		except Exception as e:
			self.call_from_thread(self._on_action_error, i, pr, e)
			return
		self.call_from_thread(self._on_action_done, i, pr, spec, result)

	def _on_action_done(
		self, i: int, pr: dict, spec: ActionSpec, result: ActionResult
	) -> None:
		ts = self._tabs[i]
		pr_id = pr["id"]
		ts.acting_pr_ids.pop(pr_id, None)
		if result == ActionResult.REMOVE:
			ts.finishing_pr_ids.add(pr_id)
			self._refresh_row(i, pr_id)
			self.set_timer(2.0, lambda: self._dim_finishing(i, pr_id))
			self.set_timer(2.3, lambda: self._remove_finished(i, pr_id))
		else:
			self._refresh_row(i, pr_id)

	def _dim_finishing(self, i: int, pr_id: str) -> None:
		if pr_id not in self._tabs[i].finishing_pr_ids:
			return
		self._refresh_row(i, pr_id, dim=True)

	def _remove_finished(self, i: int, pr_id: str) -> None:
		ts = self._tabs[i]
		if pr_id not in ts.finishing_pr_ids:
			return
		ts.finishing_pr_ids.discard(pr_id)
		ts.removed_ids.add(pr_id)
		ts.prs = [p for p in ts.prs if p["id"] != pr_id]
		table = self.query_one(f"#{ts.table_id}", DataTable)
		try:
			table.remove_row(pr_id)
		except Exception:
			pass
		if ts.prs:
			new_index = min(table.cursor_row, len(ts.prs) - 1)
			table.move_cursor(row=new_index)
			if i == self._active_index():
				self._update_status(ts.config.status_bar(ts.prs[new_index]))
		elif i == self._active_index():
			self._update_status("")

	def _on_action_error(self, i: int, pr: dict, error: Exception) -> None:
		ts = self._tabs[i]
		pr_id = pr["id"]
		ts.acting_pr_ids.pop(pr_id, None)
		self._refresh_row(i, pr_id)
		self._set_breadcrumb(f"#{pr['number']} failed: {error}", i)


def run_pr_menu(
	tabs: list[TabConfig],
	poll_seconds: int = 30,
	initial_tab: int = 0,
) -> None:
	PRMenuApp(tabs, poll_seconds, initial_tab).run()
