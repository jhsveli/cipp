from dataclasses import dataclass
from enum import Enum
from typing import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option


class ActionResult(Enum):
	REMOVE = "remove"
	KEEP = "keep"


@dataclass
class ActionSpec:
	key: str
	label: str
	handler: Callable[[dict], ActionResult]


class PRMenuApp(App):
	CSS = """
	Screen { layout: vertical; }
	#status { height: 1; padding: 0 1; color: $text-muted; }
	#footer-row { height: 1; }
	#breadcrumb { height: 1; padding: 0 1; color: $accent; width: 1fr; }
	#countdown { height: 1; padding: 0 1; color: $text-muted; width: auto; text-style: italic; }
	OptionList { height: 1fr; }
	"""

	def __init__(
		self,
		title: str,
		fetch: Callable[[], list[dict]],
		actions: list[ActionSpec],
		status_bar: Callable[[dict], str],
		poll_seconds: int,
	):
		super().__init__()
		self._title = title
		self._fetch = fetch
		self._actions = {a.key: a for a in actions}
		self._status_bar = status_bar
		self._poll_seconds = poll_seconds
		self._prs: list[dict] = []
		self._removed_ids: set[str] = set()
		self._busy = False
		self._seconds_until_refresh = poll_seconds

		self.BINDINGS = [
			Binding("q", "quit", "Quit"),
			Binding("escape", "quit", "Quit"),
			*[Binding(a.key, f"run_action('{a.key}')", a.label) for a in actions],
		]

	def compose(self) -> ComposeResult:
		yield Header()
		yield OptionList(id="options")
		yield Static("", id="status")
		with Horizontal(id="footer-row"):
			yield Static("Loading…", id="breadcrumb")
			yield Static("", id="countdown")
		yield Footer()

	def on_mount(self) -> None:
		self.title = self._title
		self.refresh_now()
		self.set_interval(self._poll_seconds, self.refresh_now)
		self.set_interval(1, self._tick_countdown)
		self._render_countdown()

	def _tick_countdown(self) -> None:
		self._seconds_until_refresh = max(0, self._seconds_until_refresh - 1)
		self._render_countdown()

	def _render_countdown(self) -> None:
		self.query_one("#countdown", Static).update(
			f"Updating in {self._seconds_until_refresh}s…"
		)

	@work(thread=True, exclusive=True, group="fetch")
	def refresh_now(self) -> None:
		self.call_from_thread(self._reset_countdown)
		try:
			prs = self._fetch()
		except Exception as e:
			self.call_from_thread(self._set_breadcrumb, f"fetch failed: {e}")
			return
		self.call_from_thread(self._apply_prs, prs)

	def _reset_countdown(self) -> None:
		self._seconds_until_refresh = self._poll_seconds
		self._render_countdown()

	def _apply_prs(self, prs: list[dict]) -> None:
		visible = [pr for pr in prs if pr["id"] not in self._removed_ids]
		old_ids = {pr["id"] for pr in self._prs}
		new_ids = {pr["id"] for pr in visible}

		if old_ids == new_ids and self._prs:
			self._prs = visible
			return

		option_list = self.query_one("#options", OptionList)
		highlighted_id = None
		if option_list.highlighted is not None and self._prs:
			if 0 <= option_list.highlighted < len(self._prs):
				highlighted_id = self._prs[option_list.highlighted]["id"]

		self._prs = visible
		option_list.clear_options()
		option_list.add_options(
			[Option(f"{pr['number']:>6} {pr['title']}", id=pr["id"]) for pr in visible]
		)

		if visible:
			next_index = 0
			if highlighted_id is not None:
				for i, pr in enumerate(visible):
					if pr["id"] == highlighted_id:
						next_index = i
						break
			option_list.highlighted = next_index
			self._update_status(visible[next_index])
		else:
			self._update_status(None)

		added = len(new_ids - old_ids)
		if added and self._prs and old_ids:
			self._set_breadcrumb(f"→ refreshed: +{added} PR(s)")
		elif not old_ids:
			self._set_breadcrumb(f"loaded {len(visible)} PR(s)")
		else:
			self._set_breadcrumb(f"refreshed: {len(visible)} PR(s)")

	def on_option_list_option_highlighted(
		self, event: OptionList.OptionHighlighted
	) -> None:
		if 0 <= event.option_index < len(self._prs):
			self._update_status(self._prs[event.option_index])

	def on_option_list_option_selected(
		self, event: OptionList.OptionSelected
	) -> None:
		# Enter triggers the first action whose key isn't a single letter modifier;
		# scripts that want enter to do something map "enter" explicitly.
		if "enter" in self._actions:
			self._run_action_on_index(event.option_index, "enter")

	def _update_status(self, pr: dict | None) -> None:
		status = self.query_one("#status", Static)
		status.update(self._status_bar(pr) if pr else "")

	def _set_breadcrumb(self, text: str) -> None:
		self.query_one("#breadcrumb", Static).update(text)

	def action_run_action(self, key: str) -> None:
		option_list = self.query_one("#options", OptionList)
		if option_list.highlighted is None:
			return
		self._run_action_on_index(option_list.highlighted, key)

	def _run_action_on_index(self, index: int, key: str) -> None:
		if self._busy:
			return
		if not (0 <= index < len(self._prs)):
			return
		spec = self._actions.get(key)
		if spec is None:
			return
		pr = self._prs[index]
		self._busy = True
		self._set_breadcrumb(f"{spec.label} #{pr['number']}…")
		self._invoke(spec, pr)

	@work(thread=True, exclusive=True, group="action")
	def _invoke(self, spec: ActionSpec, pr: dict) -> None:
		try:
			result = spec.handler(pr)
		except Exception as e:
			self.call_from_thread(self._on_action_error, pr, e)
			return
		self.call_from_thread(self._on_action_done, pr, spec, result)

	def _on_action_done(
		self, pr: dict, spec: ActionSpec, result: ActionResult
	) -> None:
		self._busy = False
		if result == ActionResult.REMOVE:
			self._removed_ids.add(pr["id"])
			self._prs = [p for p in self._prs if p["id"] != pr["id"]]
			option_list = self.query_one("#options", OptionList)
			option_list.remove_option(pr["id"])
			if self._prs:
				new_index = min(
					option_list.highlighted if option_list.highlighted is not None else 0,
					len(self._prs) - 1,
				)
				option_list.highlighted = new_index
				self._update_status(self._prs[new_index])
			else:
				self._update_status(None)
		self._set_breadcrumb(f"{spec.label} #{pr['number']} ✓")

	def _on_action_error(self, pr: dict, error: Exception) -> None:
		self._busy = False
		self._set_breadcrumb(f"#{pr['number']} failed: {error}")


def run_pr_menu(
	title: str,
	fetch: Callable[[], list[dict]],
	actions: list[ActionSpec],
	status_bar: Callable[[dict], str] = lambda pr: "",
	poll_seconds: int = 30,
) -> None:
	PRMenuApp(title, fetch, actions, status_bar, poll_seconds).run()
