from cmd import exec, exec_json
from pr_menu import ActionResult, ActionSpec, run_pr_menu

REPO = "sparebank1utvikling/app-configrepo-sb1u"
AUTHOR = "aws-plattform-image-updater"

# Sometimes, if git config name differs from github user full name,
# The PRs can be tagged with either names, must check for both.
# eg Jorgen Tu Sveli and Jørgen Tu Sveli
config_name = exec(['git', 'config', '--global', '--get', 'user.name']).strip()


def fetch_prs():
	response = exec_json(['gh', 'api', 'graphql', '-f', f"query={pr_query}", '--jq', jq])

	if 'errors' in response and len(response['errors']) > 0:
		raise RuntimeError(
			f"Query returned {len(response['errors'])} errors. First was: {response['errors'][0]['message']}"
		)

	github_username = response['user']['login']
	github_name = response['user']['name']

	def satisfies_criteria(pr):
		search_content = pr['title'] + pr['body']
		author_login = pr['author']
		return (
			github_name in search_content
			or github_username in search_content
			or config_name in search_content
			or 'dependabot' in pr['body']
			or AUTHOR in author_login
		)

	return [
		pr for pr in response['prs']
		if satisfies_criteria(pr) and pr['checkStatus'] == 'SUCCESS'
	]


def approve_and_merge(pr):
	exec(['gh', 'pr', 'review', '--approve', str(pr['number']), '-R', REPO])
	exec(['gh', 'pr', 'merge', '-s', '-R', REPO, str(pr['number'])])
	return ActionResult.REMOVE


def prod_menu():
	run_pr_menu(
		title="Pick an image update in prod for approval",
		fetch=fetch_prs,
		actions=[ActionSpec(key="enter", label="Approve + merge", handler=approve_and_merge)],
		status_bar=lambda pr: pr['body'],
	)


pr_query = """{
	viewer {
		login
		name
	}
	search(query: "type:pr state:open repo:sparebank1utvikling/app-configrepo-sb1u prod in:title review-requested:@me", type: ISSUE, first: 100) {
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
	}
"""

jq = """
{
	user: {
		login: .data.viewer.login,
		name: .data.viewer.name
	},
	prs: [.data.search.edges[].node | {
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
	}]
}"""

if __name__ == '__main__':
	prod_menu()
