# Context

Glossary of the language used in this codebase. Update inline as terms get pinned down.

## Workflow

Trunk-based development: commit straight to `main`. Do not create feature branches unless something extreme warrants it.

### Headless testing
TUI can't be driven interactively from the agent. Verify render/state logic headlessly: drive with `app.run_test()` + `await pilot.pause()`, then assert on `table.get_row(key)`. Call `app._apply_prs(i, prs)` directly with canned PR dicts (include every field the tab's labels read) to simulate a fetch result (e.g. unseen-marker) without hitting the network. To exercise the [[combined fetch]] end-to-end, monkeypatch `cipp.pr_menu.exec_json`. Run via the pipx venv python: `$(pipx environment --value PIPX_LOCAL_VENVS)/cipp/bin/python`. Editable install (`pipx install -e .`) so edits need no reinstall.

## Terms

### PR
A GitHub pull request, as returned by the `gh` CLI. Each PR carries an `id` (GraphQL node ID), `number`, `title`, `body`, `repository`, `author`, `createdAt`, and `checkStatus`.

### Tab
One source of PRs (e.g. *Reviews*, *Production*). A tab contributes one aliased block to the [[combined fetch]] (`alias`, `search_query`, `node_selection`, `jq_projection`) plus a `post_process` that turns its response slice into PRs, a status-bar formatter, and a set of [[Action]]s. Modeled by `TabConfig` and held at runtime in `TabState`. Each tab renders its PRs in a Textual `DataTable` with a `pr` column (number + title) and a [[PR-status]] column (right-aligned [[Acting]] / [[Finishing]] indicator).

### Combined fetch
All tabs share **one** GraphQL request per refresh cycle, not one-per-tab. `PRMenuApp._combined_query()` emits a shared `viewer` plus one aliased `search()` block per tab; `_combined_jq()` stitches each tab's `jq_projection` under its alias. `_fetch_all()` fires the single `gh api graphql` call, then `_apply_all()` runs each tab's `post_process` over its slice and applies the rows. One unified countdown / loading flag / failure flag drives all tabs (app-level, not per-tab) — so a failure orange-borders the status area for every tab, clearing on the next successful cycle. jq fragments are kept per-tab (rooted at `.data.<alias>`); a later refactor may move projection into Python.

The *Reviews* and *Production* tabs are not disjoint by construction — *Reviews* pulls every open PR with `review-requested:@me` across all repos. Image-updater prod PRs are kept out by **author** (`-author:app/aws-plattform-image-updater` — the `app/` prefix is required because the bot is a GitHub App; without it the author does not resolve and the unresolvable `-author:` qualifier zeroes the entire result set), not by repo or label alone: the `image-updater` label is applied asynchronously after creation, so a label-only filter left a ~0–30s window where prod PRs flashed in *Reviews*; the author is fixed at creation, closing the window. Excluding the whole configrepo would be too coarse — human-authored PRs there can still legitimately require review, so they must stay in *Reviews*.

### Action
A keystroke-bound operation on a single PR — *Approve*, *Approve + merge*, *Open in browser*. Modeled by `ActionSpec`. Returns `ActionResult.REMOVE` (the PR should leave the list) or `ActionResult.KEEP` (the PR stays).

A failing handler keeps the row in a [[Failed]] state + shows the error in the [[App status]] breadcrumb (`_on_action_error`). This rides on `cmd.exec` raising on non-zero exit (`check=True`, default): a failed shell-out (e.g. `gh pr merge`) used to return `""` and look like success, so the handler returned `REMOVE` and the row vanished while nothing happened on GitHub. Callers that legitimately exit non-zero pass `check=False` (e.g. `git config --get` for an unset key in `prod.py`).

*Merge* / *Approve + merge* go through `cmd.merge_pr(repo, number)`: it tries a **rebase** merge (`-r`), falling back to **squash** (`-s`) if the repo disallows rebase. Repos vary in permitted methods (some allow only rebase, others only squash), so trying-then-falling-back covers both without an extra capabilities query.

An action with `ActionSpec.breadcrumb` set (a `pr -> str`) is a *breadcrumb action*: it skips the [[PR-status]] column entirely (no [[Acting]] spinner / [[Finishing]] ✓) and instead flashes its message in the [[App status]] breadcrumb, auto-fading after 3s. For instant ops where a per-row indicator is noise — e.g. *My PRs* `c` Copy URL and `o` Open in browser. The fade is token-guarded (`_breadcrumb_token`) so a newer breadcrumb is never wiped by an older flash's timer.

### PR-status
The right-aligned `status` column in a [[Tab]]'s `DataTable`. Per-row: shows the row's [[Acting]] spinner, [[Finishing]] `✓ Done`, or idle `idle_label`. "PR-status" always means this column — never the [[App status]] bar.

### App status
The green-framed `#statusbar` (`Vertical`) above the preview pane. Holds one `#statusbar-row` (`Horizontal`) with two `Static`s: `#countdown` (left — PR count + refresh countdown, or [[Loading]] spinner) and `#breadcrumb` (right — transient messages: `+N new PR(s)`, [[Safeguard]] prompts, action failures). "App status" always means this bar — never the [[PR-status]] column.

### Slack post
*My PRs* `p` action — posts the highlighted PR as a link to a Slack channel, as the user. Gated on both `CIPP_SLACK_MESSAGE_USER_TOKEN` (xoxp- user token, `chat:write`) and `CIPP_SLACK_MESSAGE_CHANNEL_ID`; the `ActionSpec` is appended only when `slack.enabled()`, so absent env vars = no `p` key. Posts via `curl` to `chat.postMessage` through `cmd.exec` (same shell-out path as `gh`); no new deps. Returns KEEP. `mine.py` jq emits `url` for the link.

### Action flash
When an [[Action]] fires (after any [[Safeguard]] passes), its entry in that tab's hotkey legend gets a slight `$boost` highlight for 0.4s, then fades. Token-guarded (`_action_flash_token`) so a newer trigger isn't cleared by an older flash's timer; `_flashed_action` holds `(tab index, key)`. Applies to every action, spinner or [[App status]]-breadcrumb alike.

### Safeguard
An optional confirmation gate on an [[Action]] (`ActionSpec.safeguard`, a `Safeguard` with `when(pr) -> bool` + `descriptor`). When `when(pr)` is true, the first keypress arms it (per-row, `TabState.pending_confirm`) and shows a yellow ([[App status]] `warn` variant) breadcrumb `⚠️  Really {label} {descriptor} PR? Press {key} to confirm!`; the same key on the same row fires it. On confirm, the breadcrumb flips to a green `✅ Confirmed — {label}` (`confirmed` variant) that fades after 1s — unless a newer breadcrumb (e.g. arming another confirm) replaces it first, guarded by `_breadcrumb_token`. An armed confirm disarms (`_disarm_confirm`) on any of: cursor lands on a different PR, **3s timeout** (token-guarded by `_confirm_token`), a fetch that changes the PR set (rebuild) or a PR's `state_signature` (same-set), or switching tab (disarms across *all* tabs, since the armed row may be on the one being left). *My PRs* `m` safeguards unapproved PRs; *Reviews* `m` safeguards non-green checks; *Production* `enter` safeguards a teammate's change (`needs_confirm`). An image-update PR's GitHub author is always the bot, so `needs_confirm` extracts the human from the title ([[PR]] author heuristic) and matches it case-insensitively against `MY_IDENTITIES` — git config name + the gh-authenticated login and full name, resolved once at load. Gated only when the resolved author is a **non-bot** that isn't me: a resolved bot author is never safeguarded, nor is my own change.

### Block
A hard gate on an [[Action]] (`ActionSpec.block`, a `pr -> str | None`) checked in `_run_action_on_tab` **before** any [[Safeguard]]. When it returns a reason string the action is refused outright — a yellow `🚫 {reason}` breadcrumb flashes and the row stays; there is no arm/confirm path past it (unlike a [[Safeguard]], which a second keypress overrides). Both merge actions block on a failing status-check rollup (`checkStatus in {FAILURE, ERROR}` via `build_failing`): *Created* `m` Merge and *Reviews* `m` Approve + merge. A failing build is thus unmergeable from cipp, whereas merely non-green/unapproved is only safeguarded.

### Acting
A row's state while one of its [[Action]]s is in flight. Per-row, per-PR — distinct from *loading* (the tab is fetching the list) and from any tab-wide busy state. Visualised by a spinner + the action's label in the [[PR-status]] column. Tracked by `TabState.acting_pr_ids`.

The action key is rejected on a row that is already acting; this is the row-level lock that prevents double-firing.

### Finishing
A row's state immediately after an [[Acting]] action that returned `REMOVE` succeeded. Visualised as `✓ Done` in the [[PR-status]] column for 2 seconds, then the cell dims briefly, then the row is removed from the table. Tracked by `TabState.finishing_pr_ids`. Action keys remain locked while finishing.

### Failed
A row's state after an [[Acting]] action's handler raised (e.g. `gh pr merge`/`approve` exited non-zero — see [[Action]]). The PR **stays** in the list (it was never removed) and the [[PR-status]] column shows a red `✗ {label} failed`, persisting (unlike the transient breadcrumb). Tracked by `TabState.failed_pr_ids` (pr_id -> failed action label). Cleared on retry (pressing the action again on that row) or on any fetch (`_apply_prs` treats a fetch as fresh truth and clears all markers). Distinct from [[Finishing]], which is the *success* path.

### Unseen
A PR that arrived in a [[Tab]]'s list from a fetch *after* the initial load and has not yet been looked at. Visualised by a `●` marker prefixing the **first column** (Age / checks). A row clears its marker the moment its row is highlighted (or the cursor lands on it after a rebuild / tab activation). Tracked by `TabState.unseen_pr_ids`. The initial load never marks PRs unseen.

When an *inactive* tab gains a PR **or** any of its PRs' `state_signature` changes (e.g. checks or approval status), a `●` is appended after the count in its tab title (`TabState.has_updates`); it clears when the user switches to that tab. Each tab defines `TabConfig.state_signature` (Reviews: `checkStatus`; My PRs: `(checkStatus, review_state)`; Production: default `None` = count only); last-seen signatures live in `TabState.signatures`.

### Loading
A tab is fetching its PR list. Visualised by a spinner in the `#countdown` widget of the [[App status]] bar. Tracked by `_loading_tabs` on the app. Distinct from [[Acting]] / [[Finishing]], which are per-row.

### File jump
`option`+`PgUp`/`PgDn` (and `alt`+ for Linux/Windows — same terminal bytes, but Textual resolves binding strings separately so both are bound) scrolls the preview pane (`#status`) to the previous/next file in a diff. Files are the `diff --git` headers; offsets are computed by wrapping each diff line at the `#status-diff` content width (`_diff_file_offsets`), so `scroll_to(y=offset)` lands the header at viewport top. Current file = last header at/above `scroll_y`; target clamps at the ends. No-op unless the highlighted PR is in diff mode with a cached diff.

Each jump flashes a transient file list (`#filelist`, docked top inside the `#status` preview pane — not a full-screen overlay) listing every file with the active one marked `▶`. It hides on whichever comes first: a **1.5s timeout** (token-guarded by `_filelist_token`, re-extended by a repeated jump) or **any non-jump key** — caught in `on_event` (overridden), since priority/widget-bound keys never reach `on_key`. Terminals don't report modifier-release, so hide-on-release isn't possible.

### Refresh pause
While any row in a tab is [[Acting]] or [[Finishing]], that tab's countdown does not tick down and any in-flight fetch result is dropped on arrival. The pause prevents a refetch from rebuilding the table and wiping the row's spinner / `✓ Done` state. Refreshes resume once the tab has no acting or finishing rows.
