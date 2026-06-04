# cipp

> *Old English `cipp` — log, trunk*

A Textual TUI for skimming, approving, and merging GitHub PRs from the terminal.

Two tabs:

- **Production** — image-updater PRs in the configrepo awaiting your review (filtered to mentions, dependabot, and SUCCESS checks). Enter approves and merges.
- **Reviews** — PRs across GitHub where you're a requested reviewer, excluding image-updater. `a` approves, `m` approves and merges, `o` opens in browser.
- **My PRs** — your own open PRs. `m` merges, `o` opens in browser, `c` copies the PR URL to the clipboard, `p` posts the PR to Slack to notify the team (only when Slack is configured, see below).

## Slack

Set both env vars to enable the `p` action on **My PRs**, which posts the highlighted PR as a link to a channel, on your behalf:

```bash
export CIPP_SLACK_MESSAGE_USER_TOKEN=xoxp-…   # Slack user token with chat:write
export CIPP_SLACK_MESSAGE_CHANNEL_ID=C0123…   # target channel ID
```

The post appears as you (user token, not a bot). When either var is unset, the `p` action is hidden.

`←/→` switches tabs. `q` / `esc` quits. The active tab's hotkey legend lives in the footer; the bottom pane previews the highlighted PR's body / status.

## Install

Requires `gh` CLI installed and authenticated, and Python 3.10+.

```bash
pipx install git+https://github.com/jhsveli/cipp.git
```

Then:

```bash
cipp                  # opens on Production
cipp --tab reviews    # opens on Reviews
```

## Update

```bash
pipx upgrade cipp
```

## Develop

```bash
git clone git@github.com:jhsveli/cipp.git
cd cipp
pip install -e .
cipp
```
