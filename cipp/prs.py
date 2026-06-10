from .abbreviate import short_repo
from .cmd import exec, exec_json
from .extract_author import resolve_author
from .pr_menu import ActionResult, ActionSpec, ColumnSpec, Safeguard, TabConfig, format_age


CHECK_EMOJI = {
	'SUCCESS': '🟢',
	'PENDING': '🟡',
	'EXPECTED': '🟡',
	'SKIPPED': '🟡',
	'FAILURE': '🔴',
	'ERROR': '🔴',
}


def fetch_prs():
	response = exec_json(['gh', 'api', 'graphql', '-f', f"query={pr_query}", '--jq', jq])

	if isinstance(response, dict) and 'errors' in response and len(response['errors']) > 0:
		raise RuntimeError(
			f"Query returned {len(response['errors'])} errors. First was: {response['errors'][0]['message']}"
		)

	return response


def approve(pr):
	exec(['gh', 'pr', 'review', '--approve', str(pr['number']), '-R', pr['repository']['nameWithOwner']])
	return ActionResult.REMOVE


def approve_and_merge(pr):
	repo = pr['repository']['nameWithOwner']
	exec(['gh', 'pr', 'review', '--approve', str(pr['number']), '-R', repo])
	exec(['gh', 'pr', 'merge', '-s', '-R', repo, str(pr['number'])])
	return ActionResult.REMOVE


def open_in_browser(pr):
	exec(['gh', 'pr', 'view', '--web', '-R', pr['repository']['nameWithOwner'], str(pr['number'])])
	return ActionResult.KEEP


def fetch_diff(pr):
	return exec(['gh', 'pr', 'diff', str(pr['number']), '-R', pr['repository']['nameWithOwner']])


def author_label(pr):
	name, is_bot = resolve_author(pr['author'], is_bot=pr['isBot'])
	emoji = '🤖' if is_bot else '🧠'
	return f"{emoji} {name}"


pr_query = """{
  search(query: "type:pr state:open review-requested:@me -label:image-updater -author:app/aws-plattform-image-updater sort:created-desc", type: ISSUE, first: 100) {
    issueCount
    pageInfo {
      endCursor
      startCursor
    }
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
          repository {
          	nameWithOwner
          	name
          }
          author {
            login
            __typename
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
	fetch=fetch_prs,
	actions=[
		ActionSpec(key="a", label="Approve", handler=approve),
		ActionSpec(
			key="m",
			label="Approve + merge",
			handler=approve_and_merge,
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
	idle_label=author_label,
	diff_fetch=fetch_diff,
	state_signature=lambda pr: pr['checkStatus'],
)
