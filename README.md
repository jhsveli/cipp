# cipp

> *Old English `cipp` — log, trunk*

A Textual TUI for skimming, approving, and merging GitHub PRs from the terminal.

Tabs:


- **Created** — your own open PRs. `m` merges, `o` opens in browser, `c` copies the PR URL to the clipboard, `p` posts the PR to Slack to notify the team (only when Slack is configured, see below). PRs that conflict with the base branch show 💥 Conflicts and can't be merged.
- **Reviews** — PRs across GitHub where you're a requested reviewer, excluding image-updater. `a` approves, `m` approves and merges, `o` opens in browser.
- **Production** — image-updater PRs in the configrepo awaiting your review (filtered to mentions, dependabot, and SUCCESS checks). Enter approves and merges.
- **Apps** — your team's apps per cluster (from `shifterctl app list`) with Prometheus status: ready/desired replicas, restarts last 1h, worst pod CPU/memory vs limits, req/s, 5xx ratio, p95 latency, time since deploy, and a health label. The preview adds image tag and restart reasons (e.g. OOMKilled). Tab title shows ✅ when all is fine, ✅ (Test ⚠︎) when only test apps have concerns, ⚠ otherwise. Refreshes every 60s. Shown only when `shifterctl` is on PATH; if its login has expired, the tab starts the login itself and shows the device code to confirm in your browser. If that fails, press `l` to retry; it also opens the login page.

## Slack 

Set both env vars to enable the `p` action on **My PRs**, which posts the highlighted PR as a link to a channel, on your behalf:

```bash
export CIPP_SLACK_MESSAGE_USER_TOKEN=xoxp-…   # Slack user token with chat:write
export CIPP_SLACK_MESSAGE_CHANNEL_ID=C0123…   # target channel ID
```

The post appears as you (user token, not a bot). When either var is unset, the `p` action is hidden.

`←/→` switches tabs. `q` / `esc` quits. The active tab's hotkey legend lives in the footer; the bottom pane previews the highlighted PR's body / status.

## Update interval

Tabs refresh every 30s by default. Override with `CIPP_UPDATE_INTERVAL` (seconds); a missing, non-numeric, or non-positive value falls back to 30.

```bash
export CIPP_UPDATE_INTERVAL=10
```

## Install

Requires `gh` CLI installed and authenticated, and Python 3.10+.

```bash
pipx install git+https://github.com/jhsveli/cipp.git
```

Then:

```bash
cipp                  # opens on Production
cipp --tab reviews    # opens on Reviews
cipp --tab apps       # opens on Apps
```

## Update

When `main` is ahead of the running install (checked every 60s), the status bar shows `⬆ N new commit(s) — u to update`. Press `u`: cipp runs `pipx upgrade cipp` and restarts on the same tab. Or by hand:

```bash
pipx upgrade cipp
```

Installs from before versioning came from git (version `0.1.0`) need a one-off `pipx reinstall cipp`.

## Develop

```bash
git clone git@github.com:jhsveli/cipp.git
cd cipp
pip install -e .
cipp
```
