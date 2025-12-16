from simple_term_menu import TerminalMenu
from cmd import exec, exec_json
import json

REPO = "sparebank1utvikling/app-configrepo-sb1u"
AUTHOR = "aws-plattform-image-updater"

# Sometimes, if git config name differs from github user full name, The PRs are tagged with either names, must check for both. eg Jorgen Tu Sveli and Jørgen Tu Sveli
config_name = exec(['git', 'config', '--global', '--get', 'user.name']).strip()

def prod_menu():
	response = exec_json(['gh', 'api', 'graphql', '-f', f"query={pr_query}", '--jq', jq])
	github_username = response['user']['login']
	github_name = response['user']['name']

	def satisfies_criteria(pr):
		search_content = pr['title'] + pr['body']
		author_login = pr['author']

		return github_name in search_content or github_username in search_content or config_name in search_content or 'dependabot' in pr['body'] or AUTHOR in author_login

	# Filter based on handle or name mentioned in pr title
	prs = {str(pr['id']): pr for pr in response['prs'] if satisfies_criteria(pr) and pr['checkStatus'] == 'SUCCESS'}

	if len(prs.keys()) == 0:
		print(f"0 waiting prod prs found for {github_username} or {github_name}")
		quit()

	def render_statusbar(menuitem):
			pr = prs[menuitems_to_id[menuitem]]
			return pr['body']

	approved_prs = set()

	while True:
		menuitems_to_id = {f"{pr['number']:>6} {pr['title']}": key for key, pr in prs.items() if key not in approved_prs}
		menuchoices = list(menuitems_to_id.keys())

		if len(menuchoices) == 0:
			print("No more PRs - ", end='')
			break
		
		menu = TerminalMenu(
			menuchoices,
			title = "Pick an image update in prod for approval:",
			status_bar = render_statusbar
		)

		selected_index = menu.show()

		if selected_index is None or selected_index < 0:
			break
		pr_to_merge = prs[menuitems_to_id[menuchoices[selected_index]]]
		print(f"Approving and merging '{pr_to_merge['number']}'... ", end='', flush=True)
		exec(['gh', 'pr', 'review', '--approve', str(pr_to_merge['number']), '-R', REPO])
		print("Approved! Now merging... ", end='', flush=True)
		exec(['gh', 'pr', 'merge', '-s', '-R', REPO, str(pr_to_merge['number'])])
		print("Done!\n")
		approved_prs.add(pr_to_merge['id'])
	else:
		print("Quitting..")

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
}"""

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