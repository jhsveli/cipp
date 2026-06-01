from cmd import exec, exec_json
from pr_menu import ActionResult, ActionSpec, run_pr_menu


CHECK_EMOJI = {
	'SUCCESS': '🟢',
	'SKIPPED': '🟡',
	'FAILURE': '🔴',
}


def fetch_prs():
	response = exec_json(['gh', 'api', 'graphql', '-f', f"query={pr_query}", '--jq', jq])

	if isinstance(response, dict) and 'errors' in response and len(response['errors']) > 0:
		raise RuntimeError(
			f"Query returned {len(response['errors'])} errors. First was: {response['errors'][0]['message']}"
		)

	return response


def status_bar(pr):
	emoji = '🤖' if pr['isBot'] else '🧬'
	checkji = CHECK_EMOJI.get(pr['checkStatus'], '❔')
	return f"In: {pr['repository']['name']} | By: {emoji} {pr['author']} | On: {pr['createdAt']} | Checks: {checkji}"


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


def pr_menu():
	run_pr_menu(
		title="Open PRs awaiting review",
		fetch=fetch_prs,
		actions=[
			ActionSpec(key="a", label="Approve", handler=approve),
			ActionSpec(key="m", label="Approve + merge", handler=approve_and_merge),
			ActionSpec(key="o", label="Open in browser", handler=open_in_browser),
		],
		status_bar=status_bar,
	)


pr_query = """{
  search(query: "type:pr state:open review-requested:@me -label:image-updater", type: ISSUE, first: 100) {
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
   checkStatus: .commits.nodes[0].commit.statusCheckRollup.state
}]"""

if __name__ == '__main__':
	pr_menu()
