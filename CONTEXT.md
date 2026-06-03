# Context

Glossary of the language used in this codebase. Update inline as terms get pinned down.

## Workflow

Trunk-based development: commit straight to `main`. Do not create feature branches unless something extreme warrants it.

### Headless testing
TUI can't be driven interactively from the agent. Verify render/state logic headlessly: stub `TAB.fetch` to return canned PR dicts (include every field the tab's labels read), drive with `app.run_test()` + `await pilot.pause()`, then assert on `table.get_row(key)`. Call `app._apply_prs(i, prs)` directly to simulate a later fetch (e.g. unseen-marker). Run via the pipx venv python: `$(pipx environment --value PIPX_LOCAL_VENVS)/cipp/bin/python`. Editable install (`pipx install -e .`) so edits need no reinstall.

## Terms

### PR
A GitHub pull request, as returned by the `gh` CLI. Each PR carries an `id` (GraphQL node ID), `number`, `title`, `body`, `repository`, `author`, `createdAt`, and `checkStatus`.

### Tab
One source of PRs (e.g. *Reviews*, *Production*). A tab owns a fetch query, a status-bar formatter, and a set of [[Action]]s. Modeled by `TabConfig` and held at runtime in `TabState`. Each tab renders its PRs in a Textual `DataTable` with a `pr` column (number + title) and a `status` column (right-aligned [[Acting]] / [[Finishing]] indicator).

The *Reviews* and *Production* tabs are not disjoint by construction — *Reviews* pulls every open PR with `review-requested:@me` across all repos. Image-updater prod PRs are kept out by **author** (`-author:app/aws-plattform-image-updater` — the `app/` prefix is required because the bot is a GitHub App; without it the author does not resolve and the unresolvable `-author:` qualifier zeroes the entire result set), not by repo or label alone: the `image-updater` label is applied asynchronously after creation, so a label-only filter left a ~0–30s window where prod PRs flashed in *Reviews*; the author is fixed at creation, closing the window. Excluding the whole configrepo would be too coarse — human-authored PRs there can still legitimately require review, so they must stay in *Reviews*.

### Action
A keystroke-bound operation on a single PR — *Approve*, *Approve + merge*, *Open in browser*. Modeled by `ActionSpec`. Returns `ActionResult.REMOVE` (the PR should leave the list) or `ActionResult.KEEP` (the PR stays).

### Slack post
*My PRs* `p` action — posts the highlighted PR as a link to a Slack channel, as the user. Gated on both `CIPP_SLACK_MESSAGE_USER_TOKEN` (xoxp- user token, `chat:write`) and `CIPP_SLACK_MESSAGE_CHANNEL_ID`; the `ActionSpec` is appended only when `slack.enabled()`, so absent env vars = no `p` key. Posts via `curl` to `chat.postMessage` through `cmd.exec` (same shell-out path as `gh`); no new deps. Returns KEEP. `mine.py` jq emits `url` for the link.

### Safeguard
An optional confirmation gate on an [[Action]] (`ActionSpec.safeguard`, a `Safeguard` with `when(pr) -> bool` + `descriptor`). When `when(pr)` is true, the first keypress arms it (per-row, `TabState.pending_confirm`) and shows breadcrumb `Really {label} {descriptor} PR? Press {key} to confirm!`; the same key on the same row fires it. Moving the cursor or switching tab disarms. *My PRs* `m` safeguards unapproved PRs; *Reviews* `m` safeguards non-green checks.

### Acting
A row's state while one of its [[Action]]s is in flight. Per-row, per-PR — distinct from *loading* (the tab is fetching the list) and from any tab-wide busy state. Visualised by a spinner + the action's label in the `status` column. Tracked by `TabState.acting_pr_ids`.

The action key is rejected on a row that is already acting; this is the row-level lock that prevents double-firing.

### Finishing
A row's state immediately after an [[Acting]] action that returned `REMOVE` succeeded. Visualised as `✓ Done` in the `status` column for 2 seconds, then the cell dims briefly, then the row is removed from the table. Tracked by `TabState.finishing_pr_ids`. Action keys remain locked while finishing.

### Unseen
A PR that arrived in a [[Tab]]'s list from a fetch *after* the initial load and has not yet been looked at. Visualised by a `●` marker prefixing the **first column** (Age / checks). A row clears its marker the moment its row is highlighted (or the cursor lands on it after a rebuild / tab activation). Tracked by `TabState.unseen_pr_ids`. The initial load never marks PRs unseen.

When an *inactive* tab gains a PR **or** any of its PRs' `state_signature` changes (e.g. checks or approval status), a `●` is appended after the count in its tab title (`TabState.has_updates`); it clears when the user switches to that tab. Each tab defines `TabConfig.state_signature` (Reviews: `checkStatus`; My PRs: `(checkStatus, review_state)`; Production: default `None` = count only); last-seen signatures live in `TabState.signatures`.

### Loading
A tab is fetching its PR list. Visualised by a spinner in the countdown widget at the top of the status frame. Tracked by `_loading_tabs` on the app. Distinct from [[Acting]] / [[Finishing]], which are per-row.

### Refresh pause
While any row in a tab is [[Acting]] or [[Finishing]], that tab's countdown does not tick down and any in-flight fetch result is dropped on arrival. The pause prevents a refetch from rebuilding the table and wiping the row's spinner / `✓ Done` state. Refreshes resume once the tab has no acting or finishing rows.
