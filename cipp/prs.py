from .abbreviate import short_repo
from .cmd import exec, merge_pr
from .extract_author import resolve_author
from .pr_menu import ActionResult, ActionSpec, ColumnSpec, Safeguard, TabConfig, age_key, format_age


CHECK_EMOJI = {
	'SUCCESS': '🟢',
	'PENDING': '🟡',
	'EXPECTED': '🟡',
	'SKIPPED': '🟡',
	'FAILURE': '🔴',
	'ERROR': '🔴',
}


# Sort: worst build first when descending.
CHECK_RANK = {'SUCCESS': 0, 'PENDING': 1, 'EXPECTED': 1, 'SKIPPED': 1, 'FAILURE': 2, 'ERROR': 2}


# Reviewed-only PRs (request gone) stay until approved; bot PRs stay
# approved too, as long as unmerged (someone must still merge them).
def post_process(prs):
	return [pr for pr in prs if pr['requested'] or not pr['approved'] or is_bot(pr)]


def is_bot(pr) -> bool:
	return resolve_author(pr['author'], is_bot=pr['isBot'])[1]


def has_conflicts(pr) -> bool:
	return pr.get('mergeable') == 'CONFLICTING'


def merge_block(pr):
	if build_failing(pr):
		return "Build failing — cannot merge"
	if has_conflicts(pr):
		return "Conflicts with base branch — cannot merge"
	return None


# A failed status-check rollup blocks merging (hard block, not a confirm).
def build_failing(pr) -> bool:
	return pr.get('checkStatus') in ('FAILURE', 'ERROR')


MONOREPO = "awl-monorepo"
# Changed files, for display_repo; shared with the Created tab.
FILES_SELECTION = """
          files(first: 100) {
            nodes { path }
            pageInfo { hasNextPage }
          }
"""
FILES_PROJECTION = """
   files: [.files.nodes[]?.path],
   filesTruncated: (.files.pageInfo.hasNextPage // false),"""
GROUP_DIRS = {"internal-apps"}  # + team-*: apps/<group>/<app>/…


def touched_app(path: str) -> str | None:
	parts = path.split("/")
	if len(parts) < 3 or parts[0] != "apps":
		return None
	if parts[1].startswith("team-") or parts[1] in GROUP_DIRS:
		return parts[2] if len(parts) > 3 else None  # group-level file (e.g. team pom)
	return parts[1]


def display_repo(pr) -> str:
	# awl-monorepo PRs touching exactly one app are named by that app.
	name = pr['repository']['name']
	if name != MONOREPO or pr.get('filesTruncated') or not pr.get('files'):
		return name
	apps = {touched_app(f) for f in pr['files']}
	return apps.pop() if len(apps) == 1 and None not in apps else name


def approve(pr):
	exec(['gh', 'pr', 'review', '--approve', str(pr['number']), '-R', pr['repository']['nameWithOwner']])
	return ActionResult.REMOVE


def approve_and_merge(pr):
	repo = pr['repository']['nameWithOwner']
	exec(['gh', 'pr', 'review', '--approve', str(pr['number']), '-R', repo])
	merge_pr(repo, pr['number'])
	return ActionResult.REMOVE


def open_in_browser(pr):
	exec(['gh', 'pr', 'view', '--web', '-R', pr['repository']['nameWithOwner'], str(pr['number'])])
	return ActionResult.KEEP


def fetch_diff(pr):
	return exec(['gh', 'pr', 'diff', str(pr['number']), '-R', pr['repository']['nameWithOwner']])


def author_label(pr):
	if has_conflicts(pr):
		return "💥 Conflicts"
	name, bot = resolve_author(pr['author'], is_bot=pr['isBot'])
	emoji = '✅' if pr['approved'] else '🤖' if bot else '🧠'
	return f"{emoji} {name}"


FILTERS = "type:pr state:open -label:image-updater -author:app/aws-plattform-image-updater sort:created-desc"
SEARCH_QUERY = f"review-requested:@me {FILTERS}"
# GitHub drops the review request on any submitted review (also COMMENTED),
# so PRs I've reviewed come from a 2nd search, kept until approved.
EXTRA_SEARCH = {"reviewed": f"reviewed-by:@me -author:@me {FILTERS}"}

NODE_SELECTION = """
          url
          id
          number
          title
          createdAt
          updatedAt
          body
          state
          repository {
          	nameWithOwner
          	name
          }
          author {
            login
            __typename
          }
          reviewDecision
          mergeable
""" + FILES_SELECTION + """
          latestOpinionatedReviews(last: 20) {
            nodes { state author { login } }
          }
          commits(last: 1) {
            nodes {
              commit {
                oid
                statusCheckRollup {
				  state
				}
	          }
	        }
	      }
"""

JQ_PROJECTION = """
.data.viewer.login as $me
| ([.data.reviews.edges[].node | .requested = true]
   + [.data.reviewed.edges[].node | .requested = false])
| group_by(.id) | map(max_by(.requested)) | sort_by(.createdAt) | reverse
| [.[] | {
   requested: .requested,
   approved: (.reviewDecision == "APPROVED"
     or any(.latestOpinionatedReviews.nodes[]; .author.login == $me and .state == "APPROVED")),
   mergeable: .mergeable,""" + FILES_PROJECTION + """
   id: .id,
   number: .number,
   state: .state,
   repository: { nameWithOwner: .repository.nameWithOwner, name: .repository.name },
   author: .author.login,
   isBot: (.author.__typename == "Bot"), # Check if the type is "Bot"
   createdAt: .createdAt,
   title: .title,
   body: .body,
   headSha: .commits.nodes[0].commit.oid,
   checkStatus: .commits.nodes[0].commit.statusCheckRollup.state
}]"""

TAB = TabConfig(
	name="Review requests",
	title="Open PRs awaiting review",
	alias="reviews",
	search_query=SEARCH_QUERY,
	extra_search=EXTRA_SEARCH,
	node_selection=NODE_SELECTION,
	jq_projection=JQ_PROJECTION,
	post_process=post_process,
	actions=[
		ActionSpec(key="a", label="Approve", handler=approve),
		ActionSpec(
			key="m",
			label="Approve + merge",
			handler=approve_and_merge,
			block=merge_block,
			safeguard=Safeguard(
				when=lambda pr: pr["checkStatus"] != "SUCCESS",
				descriptor="non-green",
			),
		),
		ActionSpec(key="o", label="Open in browser", handler=open_in_browser),
	],
	status_bar=lambda pr: pr['body'],
	columns=[
		ColumnSpec(
			key="checks",
			label="✓",
			width=2,
			render=lambda pr: CHECK_EMOJI.get(pr["checkStatus"], "❔"),
			sort_key=lambda pr: CHECK_RANK.get(pr["checkStatus"]),
		),
		ColumnSpec(
			key="ref",
			label="Ref",
			width=None,
			render=lambda pr: f"{short_repo(display_repo(pr))}#{pr['number']}",
			sort_key=lambda pr: (short_repo(display_repo(pr)), pr['number']),
		),
		ColumnSpec(
			key="age",
			label="Age",
			width=6,
			render=lambda pr: format_age(pr["createdAt"]),
			sort_key=lambda pr: age_key(pr["createdAt"]),
		),
	],
	pr_label=lambda pr: pr['title'],
	idle_label=author_label,
	diff_fetch=fetch_diff,
	state_signature=lambda pr: (pr['checkStatus'], pr['approved'], has_conflicts(pr)),
)
