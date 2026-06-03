from . import slack
from .abbreviate import short_repo
from .cmd import exec, exec_json
from .pr_menu import ActionResult, ActionSpec, ColumnSpec, Safeguard, TabConfig, format_age

CHECK_EMOJI = {
	'SUCCESS': '🟢',
	'PENDING': '🟡',
	'EXPECTED': '🟡',
	'SKIPPED': '🟡',
	'FAILURE': '🔴',
	'ERROR': '🔴',
}

# Review state derived from actual reviews (reviewDecision is null when a repo
# does not *require* review, even after someone approves). approved sorts first.
REVIEW_STATE = {
	'APPROVED': ('✅ Approved', 0),
	'AWAITING': ('⌛ Awaiting review', 1),
	'CHANGES_REQUESTED': ('🔁 Changes requested', 2),
}


def review_state(pr) -> str:
	states = pr.get('reviews') or []
	if 'CHANGES_REQUESTED' in states:
		return 'CHANGES_REQUESTED'
	if 'APPROVED' in states:
		return 'APPROVED'
	return 'AWAITING'


def fetch_prs():
	response = exec_json(['gh', 'api', 'graphql', '-f', f"query={pr_query}", '--jq', jq])

	if isinstance(response, dict) and 'errors' in response and len(response['errors']) > 0:
		raise RuntimeError(
			f"Query returned {len(response['errors'])} errors. First was: {response['errors'][0]['message']}"
		)

	return sorted(response, key=lambda pr: REVIEW_STATE[review_state(pr)][1])


def merge(pr):
	exec(['gh', 'pr', 'merge', '-s', '-R', pr['repository']['nameWithOwner'], str(pr['number'])])
	return ActionResult.REMOVE


def open_in_browser(pr):
	exec(['gh', 'pr', 'view', '--web', '-R', pr['repository']['nameWithOwner'], str(pr['number'])])
	return ActionResult.KEEP


def post_to_slack(pr):
	slack.post_message(f"PR ready for review: <{pr['url']}|{pr['title']}>")
	return ActionResult.KEEP


def fetch_diff(pr):
	return exec(['gh', 'pr', 'diff', str(pr['number']), '-R', pr['repository']['nameWithOwner']])


def review_label(pr):
	return REVIEW_STATE[review_state(pr)][0]


pr_query = """{
  search(query: "type:pr state:open author:@me sort:created-desc", type: ISSUE, first: 100) {
    edges {
      node {
        ... on PullRequest {
          url
          id
          number
          title
          createdAt
          updatedAt
          body
          state
          latestOpinionatedReviews(last: 20) {
            nodes {
              state
            }
          }
          repository {
            nameWithOwner
            name
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
        }
      }
    }
  }
}"""

jq = """
[.data.search.edges[].node | {
   id: .id,
   url: .url,
   number: .number,
   state: .state,
   reviews: [.latestOpinionatedReviews.nodes[].state],
   repository: { nameWithOwner: .repository.nameWithOwner, name: .repository.name },
   createdAt: .createdAt,
   title: .title,
   body: .body,
   headSha: .commits.nodes[0].commit.oid,
   checkStatus: .commits.nodes[0].commit.statusCheckRollup.state
}]"""

TAB = TabConfig(
	name="My PRs",
	title="My open PRs",
	fetch=fetch_prs,
	actions=[
		ActionSpec(
			key="m",
			label="Merge",
			handler=merge,
			safeguard=Safeguard(
				when=lambda pr: review_state(pr) != 'APPROVED',
				descriptor="unapproved",
			),
		),
		ActionSpec(key="o", label="Open in browser", handler=open_in_browser),
		# Only offered when both Slack env vars are set; absent otherwise.
		*([ActionSpec(key="p", label="Post to Slack", handler=post_to_slack)]
		  if slack.enabled() else []),
	],
	status_bar=lambda pr: pr['body'],
	columns=[
		ColumnSpec(
			key="checks",
			label="✓",
			width=2,
			render=lambda pr: CHECK_EMOJI.get(pr["checkStatus"], "❔"),
		),
		ColumnSpec(
			key="ref",
			label="Ref",
			width=None,
			render=lambda pr: f"{short_repo(pr['repository']['name'])}#{pr['number']}",
		),
		ColumnSpec(
			key="age",
			label="Age",
			width=6,
			render=lambda pr: format_age(pr["createdAt"]),
		),
	],
	pr_label=lambda pr: pr['title'],
	idle_label=review_label,
	diff_fetch=fetch_diff,
	state_signature=lambda pr: (pr['checkStatus'], review_state(pr)),
)
