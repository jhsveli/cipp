# seon

> *Old English `sēon` — to look, to see*

A Textual TUI for skimming, approving, and merging GitHub PRs from the terminal.

Two tabs:

- **Production** — image-updater PRs in the configrepo awaiting your review (filtered to mentions, dependabot, and SUCCESS checks). Enter approves and merges.
- **Reviews** — PRs across GitHub where you're a requested reviewer, excluding image-updater. `a` approves, `m` approves and merges, `o` opens in browser.

`←/→` switches tabs. `q` / `esc` quits. The active tab's hotkey legend lives in the footer; the bottom pane previews the highlighted PR's body / status.

## Install

Requires `gh` CLI installed and authenticated, and Python 3.10+.

```bash
pipx install git+https://github.com/jhsveli/seon.git
```

Then:

```bash
seon                  # opens on Production
seon --tab reviews    # opens on Reviews
```

## Update

```bash
pipx upgrade seon
```

## Develop

```bash
git clone git@github.com:jhsveli/seon.git
cd seon
pip install -e .
seon
```
