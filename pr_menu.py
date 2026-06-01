from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from typing import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Header, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option


class ActionResult(Enum):
	REMOVE = "remove"
	KEEP = "keep"


@dataclass
class ActionSpec:
	key: str
	label: str
	handler: Callable[[dict], ActionResult]


@dataclass
class TabConfig:
	name: str
	title: str
	fetch: Callable[[], list[dict]]
	actions: list[ActionSpec]
	status_bar: Callable[[dict], str] = lambda pr: ""


@dataclass
class TabState:
	config: TabConfig
	option_list_id: str
	actions_by_key: dict[str, ActionSpec]
	prs: list[dict] = field(default_factory=list)
	removed_ids: set[str] = field(default_factory=set)
	busy: bool = False
	seconds_until_refresh: int = 0


class PRMenuApp(App):
	CSS = """
	Screen { layout: vertical; }
	#tabs { height: 2fr; }
	#status { height: 3fr; padding: 0 1; color: $text-muted; overflow-y: auto; }
	#footer-row { height: 1; }
	#breadcrumb { width: 1fr; padding: 0 1; color: $accent; }
	#countdown { width: auto; padding: 0 1; color: $text-muted; text-style: italic; }
	#hotkeys { height: 1; padding: 0 1; background: $accent 20%; color: $text; }
	OptionList { height: 1fr; }
	"""

	BINDINGS = [
		Binding("q", "quit", "Quit"),
		Binding("escape", "quit", "Quit"),
		Binding("left", "previous_tab", "Prev tab", priority=True),
		Binding("right", "next_tab", "Next tab", priority=True),
	]

	def __init__(
		self,
		tabs: list[TabConfig],
		poll_seconds: int,
		initial_tab: int,
	):
		super().__init__()
		self._poll_seconds = poll_seconds
		self._initial_tab = initial_tab
		self._tabs: list[TabState] = [
			TabState(
				config=cfg,
				option_list_id=f"options-{i}",
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
					yield OptionList(id=ts.option_list_id)
		yield Static("", id="status")
		with Horizontal(id="footer-row"):
			yield Static("Loading…", id="breadcrumb")
			yield Static("", id="countdown")
		yield Static("", id="hotkeys")

	def on_mount(self) -> None:
		self.title = self._tabs[self._initial_tab].config.title
		for i in range(len(self._tabs)):
			self._refresh_tab(i)
			self.set_interval(self._poll_seconds, partial(self._refresh_tab, i))
		self.set_interval(1, self._tick_countdown)
		self._render_countdown()
		self._render_hotkeys()

	def _active_index(self) -> int:
		active = self.query_one(TabbedContent).active
		if active and active.startswith("tab-"):
			return int(active.removeprefix("tab-"))
		return 0

	def _tick_countdown(self) -> None:
		for ts in self._tabs:
			ts.seconds_until_refresh = max(0, ts.seconds_until_refresh - 1)
		self._render_countdown()

	def _render_countdown(self) -> None:
		ts = self._tabs[self._active_index()]
		self.query_one("#countdown", Static).update(
			f"{len(ts.prs)} PR(s) · Updating in {ts.seconds_until_refresh}s…"
		)

	def _render_hotkeys(self) -> None:
		ts = self._tabs[self._active_index()]
		key_label = {"enter": "↵", "escape": "esc"}
		parts = [f"[b]{key_label.get(a.key, a.key)}[/b] {a.label}" for a in ts.config.actions]
		parts.extend(["[b]←/→[/b] Switch tab", "[b]q[/b] Quit"])
		self.query_one("#hotkeys", Static).update("  ".join(parts))

	def _refresh_tab(self, i: int) -> None:
		self.run_worker(
			lambda: self._fetch_tab(i),
			thread=True,
			exclusive=True,
			group=f"fetch-{i}",
		)

	def _fetch_tab(self, i: int) -> None:
		self.call_from_thread(self._reset_countdown, i)
		try:
			prs = self._tabs[i].config.fetch()
		except Exception as e:
			self.call_from_thread(self._set_breadcrumb, f"[{self._tabs[i].config.name}] fetch failed: {e}")
			return
		self.call_from_thread(self._apply_prs, i, prs)

	def _reset_countdown(self, i: int) -> None:
		self._tabs[i].seconds_until_refresh = self._poll_seconds
		if i == self._active_index():
			self._render_countdown()

	def _apply_prs(self, i: int, prs: list[dict]) -> None:
		ts = self._tabs[i]
		visible = [pr for pr in prs if pr["id"] not in ts.removed_ids]
		old_ids = {pr["id"] for pr in ts.prs}
		new_ids = {pr["id"] for pr in visible}

		if old_ids == new_ids and ts.prs:
			ts.prs = visible
			return

		option_list = self.query_one(f"#{ts.option_list_id}", OptionList)
		highlighted_id = None
		if option_list.highlighted is not None and ts.prs:
			if 0 <= option_list.highlighted < len(ts.prs):
				highlighted_id = ts.prs[option_list.highlighted]["id"]

		ts.prs = visible
		option_list.clear_options()
		option_list.add_options(
			[Option(f"{pr['number']:>6} {pr['title']}", id=pr["id"]) for pr in visible]
		)

		if visible:
			next_index = 0
			if highlighted_id is not None:
				for j, pr in enumerate(visible):
					if pr["id"] == highlighted_id:
						next_index = j
						break
			option_list.highlighted = next_index
			if i == self._active_index():
				self._update_status(ts.config.status_bar(visible[next_index]))
		else:
			if i == self._active_index():
				self._update_status("")

		added = len(new_ids - old_ids)
		if added and old_ids:
			self._set_breadcrumb(f"[{ts.config.name}] +{added} new PR(s)")
		if i == self._active_index():
			self._render_countdown()

	def _tab_index_for_option_list(self, option_list_id: str | None) -> int | None:
		if not option_list_id:
			return None
		for i, ts in enumerate(self._tabs):
			if ts.option_list_id == option_list_id:
				return i
		return None

	def on_option_list_option_highlighted(
		self, event: OptionList.OptionHighlighted
	) -> None:
		i = self._tab_index_for_option_list(event.option_list.id)
		if i is None or i != self._active_index():
			return
		ts = self._tabs[i]
		if 0 <= event.option_index < len(ts.prs):
			self._update_status(ts.config.status_bar(ts.prs[event.option_index]))

	def on_option_list_option_selected(
		self, event: OptionList.OptionSelected
	) -> None:
		i = self._tab_index_for_option_list(event.option_list.id)
		if i is None:
			return
		if "enter" in self._tabs[i].actions_by_key:
			self._run_action_on_tab(i, event.option_index, "enter")

	def on_tabbed_content_tab_activated(
		self, event: TabbedContent.TabActivated
	) -> None:
		i = self._active_index()
		ts = self._tabs[i]
		self.title = ts.config.title
		option_list = self.query_one(f"#{ts.option_list_id}", OptionList)
		if (
			option_list.highlighted is not None
			and 0 <= option_list.highlighted < len(ts.prs)
		):
			self._update_status(ts.config.status_bar(ts.prs[option_list.highlighted]))
		else:
			self._update_status("")
		self._render_countdown()
		self._render_hotkeys()
		option_list.focus()

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

	def _set_breadcrumb(self, text: str) -> None:
		self.query_one("#breadcrumb", Static).update(text)

	def on_key(self, event) -> None:
		i = self._active_index()
		spec = self._tabs[i].actions_by_key.get(event.key)
		if spec is None or event.key == "enter":
			return
		option_list = self.query_one(f"#{self._tabs[i].option_list_id}", OptionList)
		if option_list.highlighted is None:
			return
		event.stop()
		self._run_action_on_tab(i, option_list.highlighted, event.key)

	def _run_action_on_tab(self, i: int, index: int, key: str) -> None:
		ts = self._tabs[i]
		if ts.busy:
			return
		if not (0 <= index < len(ts.prs)):
			return
		spec = ts.actions_by_key.get(key)
		if spec is None:
			return
		pr = ts.prs[index]
		ts.busy = True
		self._set_breadcrumb(f"[{ts.config.name}] {spec.label} #{pr['number']}…")
		self.run_worker(
			lambda: self._invoke(i, spec, pr),
			thread=True,
			exclusive=True,
			group=f"action-{i}",
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
		ts.busy = False
		if result == ActionResult.REMOVE:
			ts.removed_ids.add(pr["id"])
			ts.prs = [p for p in ts.prs if p["id"] != pr["id"]]
			option_list = self.query_one(f"#{ts.option_list_id}", OptionList)
			option_list.remove_option(pr["id"])
			if ts.prs:
				new_index = min(
					option_list.highlighted if option_list.highlighted is not None else 0,
					len(ts.prs) - 1,
				)
				option_list.highlighted = new_index
				if i == self._active_index():
					self._update_status(ts.config.status_bar(ts.prs[new_index]))
			elif i == self._active_index():
				self._update_status("")
		self._set_breadcrumb(f"[{ts.config.name}] {spec.label} #{pr['number']} ✓")

	def _on_action_error(self, i: int, pr: dict, error: Exception) -> None:
		ts = self._tabs[i]
		ts.busy = False
		self._set_breadcrumb(f"[{ts.config.name}] #{pr['number']} failed: {error}")


def run_pr_menu(
	tabs: list[TabConfig],
	poll_seconds: int = 30,
	initial_tab: int = 0,
) -> None:
	PRMenuApp(tabs, poll_seconds, initial_tab).run()
