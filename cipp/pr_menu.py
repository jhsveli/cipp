from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from rich.console import Console
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Header, Markdown, Static, TabbedContent, TabPane


class ActionResult(Enum):
	REMOVE = "remove"
	KEEP = "keep"


@dataclass
class Safeguard:
	when: Callable[[dict], bool]  # True => the action on this PR needs confirmation
	descriptor: str = "unsafe"  # fills "Really {label} {descriptor} PR?"


@dataclass
class ActionSpec:
	key: str
	label: str
	handler: Callable[[dict], ActionResult]
	safeguard: Safeguard | None = None
	# When set, the action shows a transient (auto-fading) breadcrumb instead of
	# the Acting spinner / Finishing ✓ in the PR-status column. For instant ops
	# like copy-to-clipboard where a per-row indicator is noise.
	breadcrumb: Callable[[dict], str] | None = None


def format_age(created_at: str) -> str:
	created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
	delta = datetime.now(timezone.utc) - created
	total_minutes = int(delta.total_seconds() // 60)
	if total_minutes < 60:
		return f"{total_minutes}m"
	hours, minutes = divmod(total_minutes, 60)
	if hours < 10:
		return f"{hours}h {minutes}m"
	if hours < 24:
		return f"{hours}h"
	return f"{hours // 24}d"


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
	idle_label: Callable[[dict], str] = lambda pr: ""
	diff_fetch: Callable[[dict], str] | None = None
	# Per-PR signature of the state worth flagging on the tab title; a change
	# (in checks, approval, etc.) marks an inactive tab as updated.
	state_signature: Callable[[dict], Any] = lambda pr: None


@dataclass
class TabState:
	config: TabConfig
	table_id: str
	actions_by_key: dict[str, ActionSpec]
	prs: list[dict] = field(default_factory=list)
	removed_ids: set[str] = field(default_factory=set)
	acting_pr_ids: dict[str, str] = field(default_factory=dict)
	finishing_pr_ids: set[str] = field(default_factory=set)
	unseen_pr_ids: set[str] = field(default_factory=set)
	has_updates: bool = False
	signatures: dict[str, Any] = field(default_factory=dict)  # pr_id -> last state_signature
	pending_confirm: tuple[str, str] | None = None  # (pr_id, action key) armed for confirm
	seconds_until_refresh: int = 0


class PRMenuApp(App):
	CSS = """
	Screen { layout: vertical; background: $surface; }
	#statusbar { height: 3; border: round $success; padding: 0 1; }
	#statusbar-row { height: 1; }
	#tabs { height: 2fr; }
	#tabs > ContentTabs { margin-top: 1; }
	#tabs Tab.-active { background: $block-cursor-background; color: black; }
	#status { height: 3fr; padding: 0 1; color: $text-muted; overflow-y: auto; border: round gray; border-title-color: gray; }
	#status-diff { display: none; }
	#status.diff-mode #status-md { display: none; }
	#status.diff-mode #status-diff { display: block; }
	#countdown { width: auto; padding: 0 1; color: white; text-style: italic; }
	#countdown.running { color: $success; }
	#breadcrumb { width: 1fr; padding: 0 1; color: white; text-align: right; }
	#breadcrumb.running { color: $success; }
	#breadcrumb.warn { color: $warning; }
	#breadcrumb.confirmed { color: $success; }
	.hotkeys { height: 1; padding: 0 1; color: $text; }
	Tab.updated { color: white; text-style: not bold; }
	DataTable { height: 1fr; background: $surface; }
	DataTable:focus { background-tint: 0%; }
	#filelist { dock: top; width: 100%; height: auto; max-height: 50%; padding: 0 1; display: none;
		border: round $accent; border-title-color: $accent; background: $panel; overflow-y: auto; }
	#filelist.visible { display: block; }
	"""

	BINDINGS = [
		Binding("q", "quit", "Quit"),
		Binding("escape", "quit", "Quit"),
		Binding("left", "previous_tab", "Prev tab", priority=True),
		Binding("right", "next_tab", "Next tab", priority=True),
		Binding("d", "toggle_diff", "Toggle diff", priority=True),
		Binding("shift+up", "preview_scroll_up", "Preview ↑", priority=True),
		Binding("shift+down", "preview_scroll_down", "Preview ↓", priority=True),
		Binding("shift+pageup", "preview_page_up", "Preview ⇞", priority=True),
		Binding("shift+pagedown", "preview_page_down", "Preview ⇟", priority=True),
		# Jump between files in the diff preview. option+ (macOS) and alt+ (Linux/Windows)
		# send the same terminal sequence, but Textual resolves the binding strings
		# separately, so both spellings are bound.
		Binding("option+pageup", "jump_file(-1)", "Prev file", priority=True),
		Binding("option+pagedown", "jump_file(1)", "Next file", priority=True),
		Binding("alt+pageup", "jump_file(-1)", "Prev file", priority=True, show=False),
		Binding("alt+pagedown", "jump_file(1)", "Next file", priority=True, show=False),
	]

	SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
	NEW_MARKER = "●"

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
		self._diff_mode_pr_ids: set[str] = set()
		self._diff_cache: dict[str, tuple[str, str]] = {}
		self._breadcrumb_token = 0
		self._flashed_action: tuple[int, str] | None = None  # (tab index, action key)
		self._action_flash_token = 0
		self._confirm_token = 0  # guards the per-arm 3s confirm timeout
		self._filelist_token = 0  # guards the file-list overlay's auto-hide timer
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
		filelist = Static("", id="filelist", markup=False)
		filelist.border_title = "Files"
		status = VerticalScroll(
			filelist,
			Markdown("", id="status-md"),
			Static("", id="status-diff", markup=False),
			id="status",
		)
		status.border_title = "Preview | Body"
		yield status

	def on_mount(self) -> None:
		self.title = self._tabs[self._initial_tab].config.title
		for ts in self._tabs:
			table = self.query_one(f"#{ts.table_id}", DataTable)
			for j, col in enumerate(ts.config.columns):
				# First column carries the unseen "● " marker, so reserve 2 extra cells.
				width = col.width + 2 if j == 0 and col.width is not None else col.width
				table.add_column(col.label, key=col.key, width=width)
			table.add_column("PR", key="pr", width=10)
			table.add_column("Status", key="status", width=20)
		for i in range(len(self._tabs)):
			self._refresh_tab(i)
		self.set_interval(1, self._tick_countdown)
		self.set_interval(0.1, self._tick_spinner)
		self._render_countdown()
		self._render_hotkeys()
		self._render_tab_labels()

	def on_resize(self, event) -> None:
		self._flex_pr_columns()

	def _flex_pr_columns(self) -> None:
		for ts in self._tabs:
			try:
				table = self.query_one(f"#{ts.table_id}", DataTable)
			except Exception:
				continue
			if table.size.width <= 0:
				continue
			pr_col = next(
				(c for k, c in table.columns.items() if k.value == "pr"), None
			)
			if pr_col is None:
				continue
			others = sum(
				c.get_render_width(table) for k, c in table.columns.items() if k.value != "pr"
			)
			padding = 2 * table.cell_padding
			available = max(10, table.size.width - others - padding)
			pr_col.width = available
			pr_col.auto_width = False
			table._require_update_dimensions = True
			table.refresh()

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

	def _marker_col_key(self, ts: TabState) -> str:
		return ts.config.columns[0].key if ts.config.columns else "pr"

	def _marker_cell(self, ts: TabState, pr: dict) -> Text:
		if ts.config.columns:
			value = str(ts.config.columns[0].render(pr))
		else:
			value = ts.config.pr_label(pr)
		if pr["id"] in ts.unseen_pr_ids:
			return Text.assemble((f"{self.NEW_MARKER} ", "bold cyan"), value)
		return Text(f"  {value}")

	def _right_cell(self, ts: TabState, pr_id: str, dim: bool = False) -> Text:
		if pr_id in ts.acting_pr_ids:
			label = ts.acting_pr_ids[pr_id]
			content = f"{self.SPINNER_FRAMES[self._spinner_frame]} {label}"
		elif pr_id in ts.finishing_pr_ids:
			content = "✓ Done"
		else:
			pr = next((p for p in ts.prs if p["id"] == pr_id), None)
			content = ts.config.idle_label(pr) if pr else ""
		return Text(content, style="dim" if dim else "", justify="right")

	def _refresh_row(self, i: int, pr_id: str, dim: bool = False) -> None:
		ts = self._tabs[i]
		table = self.query_one(f"#{ts.table_id}", DataTable)
		try:
			table.update_cell(pr_id, "status", self._right_cell(ts, pr_id, dim=dim))
		except Exception:
			pass

	def _render_tab_labels(self) -> None:
		try:
			tabbed = self.query_one(TabbedContent)
		except Exception:
			return
		for i, ts in enumerate(self._tabs):
			try:
				tab = tabbed.get_tab(f"tab-{i}")
			except Exception:
				continue
			marker = f" {self.NEW_MARKER}" if ts.has_updates else ""
			tab.label = f"{ts.config.name} ({len(ts.prs)}){marker}"
			tab.set_class(ts.has_updates, "updated")

	def _render_hotkeys(self) -> None:
		key_label = {"enter": "↵", "escape": "esc"}
		for i, ts in enumerate(self._tabs):
			flashed = self._flashed_action[1] if self._flashed_action and self._flashed_action[0] == i else None
			parts = []
			for a in ts.config.actions:
				part = f" [b]{key_label.get(a.key, a.key)}[/b] {a.label} "
				# Reverse fg/bg on the just-triggered action; padding is static so layout never shifts.
				parts.append(f"[reverse]{part}[/]" if a.key == flashed else part)
			if ts.config.diff_fetch is not None:
				parts.append(" [b]d[/b] Toggle diff ")
			parts.extend([" [b]←/→[/b] Switch tab ", " [b]q[/b] Quit "])
			self.query_one(f"#hotkeys-{i}", Static).update(" ".join(parts))

	def _flash_action(self, i: int, key: str, seconds: float = 0.4) -> None:
		self._flashed_action = (i, key)
		self._action_flash_token += 1
		token = self._action_flash_token
		self._render_hotkeys()
		self.set_timer(seconds, lambda: self._clear_action_flash_if(token))

	def _clear_action_flash_if(self, token: int) -> None:
		if self._action_flash_token == token:
			self._flashed_action = None
			self._render_hotkeys()

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

	def _detect_updates(self, i: int, visible: list[dict], had_prs: bool) -> bool:
		# Compare each PR's state_signature against last seen; a new PR or a
		# changed signature counts as an update. Refreshes stored signatures.
		ts = self._tabs[i]
		sig = ts.config.state_signature
		new_sigs = {pr["id"]: sig(pr) for pr in visible}
		changed = had_prs and any(
			pid not in ts.signatures or ts.signatures[pid] != s
			for pid, s in new_sigs.items()
		)
		ts.signatures = new_sigs
		return changed and i != self._active_index()

	def _apply_prs(self, i: int, prs: list[dict]) -> None:
		ts = self._tabs[i]
		if ts.acting_pr_ids or ts.finishing_pr_ids:
			self._mark_loading(i, False)
			return
		visible = [pr for pr in prs if pr["id"] not in ts.removed_ids]
		old_ids = {pr["id"] for pr in ts.prs}
		new_ids = {pr["id"] for pr in visible}

		if old_ids == new_ids and ts.prs:
			# A changed state_signature means the basis for an armed confirm moved
			# under it — disarm. (_detect_updates is active-tab-gated, so check raw.)
			sig = ts.config.state_signature
			if ts.pending_confirm and any(
				ts.signatures.get(pr["id"]) != sig(pr) for pr in visible
			):
				self._disarm_confirm(i)
			if self._detect_updates(i, visible, had_prs=True):
				ts.has_updates = True
			ts.prs = visible
			table = self.query_one(f"#{ts.table_id}", DataTable)
			for pr in visible:
				for j, col in enumerate(ts.config.columns):
					value = self._marker_cell(ts, pr) if j == 0 else col.render(pr)
					try:
						table.update_cell(pr["id"], col.key, value)
					except Exception:
						pass
				# The status column (idle_label) can change too, e.g. approval state.
				self._refresh_row(i, pr["id"])
			self._mark_loading(i, False)
			self._render_tab_labels()
			return

		table = self.query_one(f"#{ts.table_id}", DataTable)
		highlighted_id = None
		if ts.prs and table.row_count > 0:
			cursor = table.cursor_row
			if 0 <= cursor < len(ts.prs):
				highlighted_id = ts.prs[cursor]["id"]

		# The PR set changed (rebuild) — the armed row may have moved or vanished.
		self._disarm_confirm(i)
		ts.unseen_pr_ids &= new_ids
		if old_ids:
			ts.unseen_pr_ids |= new_ids - old_ids
		if self._detect_updates(i, visible, had_prs=bool(old_ids)):
			ts.has_updates = True

		ts.prs = visible
		table.clear()
		for pr in visible:
			cells = [
				self._marker_cell(ts, pr) if j == 0 else col.render(pr)
				for j, col in enumerate(ts.config.columns)
			]
			cells.append(self._left_cell(ts, pr))
			cells.append(self._right_cell(ts, pr["id"]))
			table.add_row(*cells, key=pr["id"])
		self.call_after_refresh(self._flex_pr_columns)

		if visible:
			next_index = 0
			if highlighted_id is not None:
				for j, pr in enumerate(visible):
					if pr["id"] == highlighted_id:
						next_index = j
						break
			table.move_cursor(row=next_index)
			if i == self._active_index():
				self._mark_seen(ts, visible[next_index]["id"])
				self._render_preview(ts, visible[next_index])
		else:
			if i == self._active_index():
				self._update_status("")

		self._mark_loading(i, False)
		added = len(new_ids - old_ids)
		if added and old_ids:
			self._set_breadcrumb(f"+{added} new PR(s)", i)
		self._render_tab_labels()
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
			pr = ts.prs[event.cursor_row]
			if ts.pending_confirm and ts.pending_confirm[0] != pr["id"]:
				self._disarm_confirm(i)
			self._mark_seen(ts, pr["id"])
			self._render_preview(ts, pr)

	def _mark_seen(self, ts: TabState, pr_id: str) -> None:
		if pr_id not in ts.unseen_pr_ids:
			return
		ts.unseen_pr_ids.discard(pr_id)
		pr = next((p for p in ts.prs if p["id"] == pr_id), None)
		if pr is None:
			return
		table = self.query_one(f"#{ts.table_id}", DataTable)
		try:
			table.update_cell(pr_id, self._marker_col_key(ts), self._marker_cell(ts, pr))
		except Exception:
			pass

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
		ts.has_updates = False
		# Disarm any armed confirm (could be on the tab we just left).
		for j in range(len(self._tabs)):
			self._disarm_confirm(j)
		table = self.query_one(f"#{ts.table_id}", DataTable)
		if table.row_count > 0 and 0 <= table.cursor_row < len(ts.prs):
			pr = ts.prs[table.cursor_row]
			self._mark_seen(ts, pr["id"])
			self._render_preview(ts, pr)
		else:
			self._update_status("")
		self._render_countdown()
		self._render_tab_labels()
		self._render_hotkeys()
		self._set_breadcrumb("")
		table.focus()
		self.call_after_refresh(self._flex_pr_columns)

	def action_previous_tab(self) -> None:
		self._switch_tab(-1)

	def action_next_tab(self) -> None:
		self._switch_tab(1)

	def _switch_tab(self, delta: int) -> None:
		n = len(self._tabs)
		new_index = (self._active_index() + delta) % n
		self.query_one(TabbedContent).active = f"tab-{new_index}"

	def _update_status(self, text: str) -> None:
		self.query_one("#status-md", Markdown).update(text)

	def _show_diff_mode(self, on: bool) -> None:
		status = self.query_one("#status")
		status.set_class(on, "diff-mode")
		status.border_title = "Preview | Diff" if on else "Preview | Body"

	def _render_diff_text(self, diff: str) -> Text:
		text = Text()
		for line in diff.splitlines(keepends=False):
			if line.startswith("+++") or line.startswith("---"):
				style = "bold"
			elif line.startswith("+"):
				style = "green"
			elif line.startswith("-"):
				style = "red"
			elif line.startswith("@@"):
				style = "cyan"
			elif line.startswith("diff --git") or line.startswith("index "):
				style = "bold"
			else:
				style = ""
			text.append(line + "\n", style=style)
		return text

	def _render_preview(self, ts: TabState, pr: dict) -> None:
		pr_id = pr["id"]
		if pr_id not in self._diff_mode_pr_ids:
			self._show_diff_mode(False)
			self._update_status(ts.config.status_bar(pr))
			return
		cached = self._diff_cache.get(pr_id)
		if cached and cached[0] == pr.get("headSha"):
			self._show_diff_mode(True)
			self.query_one("#status-diff", Static).update(self._render_diff_text(cached[1]))
			return
		self._show_diff_mode(False)
		self._update_status("Loading diff...")
		self._fetch_diff(ts, pr)

	def _fetch_diff(self, ts: TabState, pr: dict) -> None:
		fetcher = ts.config.diff_fetch
		if fetcher is None:
			return
		pr_id = pr["id"]
		head_sha = pr.get("headSha", "")
		self.run_worker(
			lambda: self._fetch_diff_worker(pr_id, head_sha, pr, fetcher),
			thread=True,
			exclusive=True,
			group=f"diff-{pr_id}",
		)

	def _fetch_diff_worker(
		self,
		pr_id: str,
		head_sha: str,
		pr: dict,
		fetcher: Callable[[dict], str],
	) -> None:
		try:
			diff = fetcher(pr)
		except Exception as e:
			self.call_from_thread(self._on_diff_error, pr_id, e)
			return
		self.call_from_thread(self._on_diff_loaded, pr_id, head_sha, diff)

	def _on_diff_loaded(self, pr_id: str, head_sha: str, diff: str) -> None:
		self._diff_cache[pr_id] = (head_sha, diff)
		if pr_id in self._diff_mode_pr_ids and self._is_highlighted(pr_id):
			self._show_diff_mode(True)
			self.query_one("#status-diff", Static).update(self._render_diff_text(diff))

	def _on_diff_error(self, pr_id: str, error: Exception) -> None:
		if pr_id in self._diff_mode_pr_ids and self._is_highlighted(pr_id):
			self._show_diff_mode(False)
			self._update_status(f"Failed to fetch diff: {error}")

	def _is_highlighted(self, pr_id: str) -> bool:
		i = self._active_index()
		ts = self._tabs[i]
		try:
			table = self.query_one(f"#{ts.table_id}", DataTable)
		except Exception:
			return False
		if not (0 <= table.cursor_row < len(ts.prs)):
			return False
		return ts.prs[table.cursor_row]["id"] == pr_id

	def action_preview_scroll_up(self) -> None:
		self.query_one("#status").scroll_up()

	def action_preview_scroll_down(self) -> None:
		self.query_one("#status").scroll_down()

	def action_preview_page_up(self) -> None:
		self.query_one("#status").scroll_page_up()

	def action_preview_page_down(self) -> None:
		self.query_one("#status").scroll_page_down()

	def _current_diff(self) -> str | None:
		# The raw diff string for the highlighted PR, only while it's in diff mode.
		i = self._active_index()
		ts = self._tabs[i]
		table = self.query_one(f"#{ts.table_id}", DataTable)
		if not (0 <= table.cursor_row < len(ts.prs)):
			return None
		pr = ts.prs[table.cursor_row]
		pr_id = pr["id"]
		if pr_id not in self._diff_mode_pr_ids:
			return None
		cached = self._diff_cache.get(pr_id)
		if not cached or cached[0] != pr.get("headSha"):
			return None
		return cached[1]

	def _diff_file_offsets(self, diff: str) -> list[tuple[int, str]]:
		# Map each `diff --git` file header to its visual line offset inside the
		# preview, accounting for line-wrapping at the Static's content width — so
		# scroll_to(y=offset) lands the header at the top of the viewport.
		diff_widget = self.query_one("#status-diff", Static)
		width = diff_widget.content_region.width
		if width <= 0:
			width = 80
		console = Console(width=width)
		offsets: list[tuple[int, str]] = []
		y = 0
		for line in diff.splitlines():
			if line.startswith("diff --git"):
				offsets.append((y, self._diff_file_name(line)))
			wrapped = len(Text(line).wrap(console, width)) or 1
			y += wrapped
		return offsets

	@staticmethod
	def _diff_file_name(header: str) -> str:
		# `diff --git a/path/to/file b/path/to/file` -> `path/to/file`.
		parts = header.split()
		for token in parts:
			if token.startswith("b/"):
				return token[2:]
		if len(parts) >= 4 and parts[2].startswith("a/"):
			return parts[2][2:]
		return header

	def action_jump_file(self, delta: int) -> None:
		diff = self._current_diff()
		if diff is None:
			return
		offsets = self._diff_file_offsets(diff)
		if len(offsets) < 2:
			# Single-file (or empty) diff — nothing to jump between, but still flash
			# the list so the gesture isn't a silent no-op.
			if offsets:
				self._show_filelist(offsets, 0)
			return
		status = self.query_one("#status")
		scroll_y = round(status.scroll_y)
		# Current file = the last header at or above the current scroll position.
		current = 0
		for idx, (y, _name) in enumerate(offsets):
			if y <= scroll_y + 1:
				current = idx
			else:
				break
		target = max(0, min(len(offsets) - 1, current + delta))
		status.scroll_to(y=offsets[target][0], animate=False)
		self._show_filelist(offsets, target)

	def _show_filelist(self, offsets: list[tuple[int, str]], active: int) -> None:
		body = Text()
		for idx, (_y, name) in enumerate(offsets):
			marker = "▶ " if idx == active else "  "
			style = "bold cyan" if idx == active else "dim"
			body.append(marker + name + "\n", style=style)
		if body.plain.endswith("\n"):
			body.remove_suffix("\n")
		frame = self.query_one("#filelist", Static)
		frame.border_title = f"Files ({active + 1}/{len(offsets)})"
		frame.update(body)
		frame.add_class("visible")
		# Auto-hide after 1.5s; a repeated jump re-extends the window. Hiding also
		# happens on any other keypress (see on_key), whichever comes first.
		self._filelist_token += 1
		token = self._filelist_token
		self.set_timer(1.5, lambda: self._hide_filelist_if(token))

	def _hide_filelist(self) -> None:
		self._filelist_token += 1  # invalidate any pending auto-hide timer
		try:
			self.query_one("#filelist", Static).remove_class("visible")
		except Exception:
			pass

	def _hide_filelist_if(self, token: int) -> None:
		if self._filelist_token == token:
			self._hide_filelist()

	def _filelist_visible(self) -> bool:
		try:
			return self.query_one("#filelist", Static).has_class("visible")
		except Exception:
			return False

	def action_toggle_diff(self) -> None:
		i = self._active_index()
		ts = self._tabs[i]
		if ts.config.diff_fetch is None:
			return
		try:
			table = self.query_one(f"#{ts.table_id}", DataTable)
		except Exception:
			return
		if not (0 <= table.cursor_row < len(ts.prs)):
			return
		pr = ts.prs[table.cursor_row]
		pr_id = pr["id"]
		if pr_id in self._diff_mode_pr_ids:
			self._diff_mode_pr_ids.discard(pr_id)
		else:
			self._diff_mode_pr_ids.add(pr_id)
		self._render_preview(ts, pr)

	def _set_breadcrumb(
		self,
		text: str,
		tab_index: int | None = None,
		running: bool = False,
		variant: str | None = None,
	) -> None:
		if tab_index is not None and tab_index != self._active_index():
			return
		# Any explicit set invalidates a pending flash-clear (see _flash_breadcrumb).
		self._breadcrumb_token += 1
		breadcrumb = self.query_one("#breadcrumb", Static)
		breadcrumb.update(text)
		breadcrumb.set_class(running, "running")
		# Colour variants: "warn" (yellow, arming confirm), "confirmed" (green, fired).
		breadcrumb.set_class(variant == "warn", "warn")
		breadcrumb.set_class(variant == "confirmed", "confirmed")

	def _flash_breadcrumb(
		self, i: int, text: str, seconds: float = 3.0, variant: str | None = None
	) -> None:
		# Show a transient message, then clear it after `seconds` — but only if no
		# newer breadcrumb has replaced it in the meantime.
		self._set_breadcrumb(text, i, variant=variant)
		token = self._breadcrumb_token
		self.set_timer(seconds, lambda: self._clear_breadcrumb_if(token, i))

	def _clear_breadcrumb_if(self, token: int, i: int) -> None:
		if self._breadcrumb_token == token:
			self._set_breadcrumb("", i)

	_JUMP_FILE_KEYS = frozenset(
		{"option+pageup", "option+pagedown", "alt+pageup", "alt+pagedown"}
	)

	async def on_event(self, event) -> None:
		# App-level entry point for every event, including keys consumed by priority
		# or widget bindings (which never reach on_key). Any key other than a
		# file-jump dismisses the transient file-list overlay; the jump keys
		# re-extend it via action_jump_file instead.
		if (
			isinstance(event, events.Key)
			and self._filelist_visible()
			and event.key not in self._JUMP_FILE_KEYS
		):
			self._hide_filelist()
		await super().on_event(event)

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

	def _arm_confirm(self, i: int, pr_id: str, key: str, prompt: str) -> None:
		ts = self._tabs[i]
		ts.pending_confirm = (pr_id, key)
		self._set_breadcrumb(prompt, i, variant="warn")
		# Auto-disarm after 3s unless something disarms/re-arms first.
		self._confirm_token += 1
		token = self._confirm_token
		self.set_timer(3.0, lambda: self._disarm_confirm(i, token=token))

	def _disarm_confirm(self, i: int, token: int | None = None) -> None:
		# token set => a timeout firing; ignore if a newer arm has superseded it.
		if token is not None and token != self._confirm_token:
			return
		ts = self._tabs[i]
		if ts.pending_confirm is None:
			return
		ts.pending_confirm = None
		self._confirm_token += 1  # invalidate any pending timeout
		self._set_breadcrumb("", i)

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
		was_confirmed = False
		if spec.safeguard and spec.safeguard.when(pr):
			armed = ts.pending_confirm == (pr_id, key)
			if not armed:
				self._arm_confirm(
					i, pr_id, key,
					f"⚠️  Really {spec.label} {spec.safeguard.descriptor} PR? Press {key} to confirm!",
				)
				return
			was_confirmed = True
		ts.pending_confirm = None
		self._confirm_token += 1  # invalidate the arm's pending timeout
		if was_confirmed:
			# Flip the warning to a green ✅ acknowledgement that fades after 1s.
			self._flash_breadcrumb(i, f"✅ Confirmed — {spec.label}", seconds=1.0, variant="confirmed")
		self._flash_action(i, key)
		# Breadcrumb actions skip the PR-status spinner entirely; the row never
		# enters Acting/Finishing, feedback lives only in the App status bar.
		if spec.breadcrumb is None:
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
		if spec.breadcrumb is not None:
			self._flash_breadcrumb(i, spec.breadcrumb(pr))
			return
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
		self._diff_mode_pr_ids.discard(pr_id)
		self._diff_cache.pop(pr_id, None)
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
				self._render_preview(ts, ts.prs[new_index])
		elif i == self._active_index():
			self._update_status("")
		self._render_tab_labels()

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
