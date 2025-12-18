from cmd import exec, exec_json
import json

from simple_term_menu import TerminalMenu

from pprint import pprint

class bcolors:
    HEADER = '\x1b[7;30;47m'
    OKBLUE = '\x1b[7;30;44m'
    OKCYAN = '\x1b[7;3;45m'
    OKGREEN = '\x1b[7;30;42m'
    WARNING = '\x1b[7;30;43m'
    FAIL = '\x1b[1;31;40m'
    ENDC = '\x1b[0m'

def pr_menu():	

	print("Finding open prs with 🟢 checks..\n")

	response = exec_json(['gh', 'api', 'graphql', '-f', f"query={pr_query}", '--jq', jq])

	if 'errors' in response and len(response['errors']) > 0:
		print(f"Query returned {len(response['errors'])} errors. First was:\n{response['errors'][0]['message']})")
		quit()

	prs = {str(pr['id']): pr for pr in response}

	if len(prs.keys()) == 0:
		print(f"{bcolors.WARNING}0 waiting prs{bcolors.ENDC} found")
		quit()
	else:
		print(f"Found {bcolors.OKBLUE}{len(prs.keys())}{bcolors.ENDC} prs. Select a pr to {bcolors.OKGREEN}[a]pprove{bcolors.ENDC}, {bcolors.OKCYAN}[m]erge{bcolors.ENDC} or {bcolors.OKBLUE}[o]open{bcolors.ENDC} in browser:")

	def render_statusbar(menuitem):	
		pr = prs[menuitems_to_id[menuitem]]
		emoji = '🤖' if pr['isBot'] == 'true' else '🧬'

		match pr['checkStatus']:
			case 'SUCCESS':
				checkji = '🟢'
			case 'SKIPPED':
				checkji = '🟡'
			case 'FAILURE':
				 checkji = '🔴'
			case _:
				checkji = '❔'

		return f"In: {pr['repository']['name']} | By: {emoji} {pr['author']} | On: {pr['createdAt']} | Checks: {checkji}"

	approved_prs = set()

	while True:
		menuitems_to_id = {f"{pr['number']:>6} {pr['title']}": key for key, pr in prs.items() if key not in approved_prs}
		menuchoices = list(menuitems_to_id.keys())
		
		if len(menuchoices) == 0:
			print("No more PRs")
			break
		
		menu = TerminalMenu(
			menuchoices,
			title = '',	
			accept_keys = ('enter', 'a', 'm', 'o'),
			status_bar = render_statusbar
			)
		selected_index = menu.show()

		if selected_index is None or selected_index < 0:
			break

		accept_key_pressed = menu.chosen_accept_key
		pr_to_merge = prs[menuitems_to_id[menuchoices[selected_index]]]
		
		pr_number = pr_to_merge['number']
		pr_repo = pr_to_merge['repository']['nameWithOwner']

		if accept_key_pressed in ['a', 'm']:
			print(f"Approving '{pr_to_merge['number']}'... ", end="", flush=True)
			exec(['gh', 'pr', 'review', '--approve', str(pr_number), '-R', pr_repo])
			
		if accept_key_pressed == 'm':
			print(f"{bcolors.OKGREEN}Approved{bcolors.ENDC}! Now merging... ", end="", flush=True)
			exec(['gh', 'pr', 'merge', '-s', '-R', pr_repo, str(pr_number)])
			print(f"{bcolors.OKCYAN}Merged{bcolors.ENDC}!\n")
			approved_prs.add(pr_to_merge['id'])
		elif accept_key_pressed == 'a':			
			approved_prs.add(pr_to_merge['id'])
			print(f"{bcolors.OKGREEN}Approved{bcolors.ENDC}!\n")
		elif accept_key_pressed == 'o':
			print(f"Opening PR {pr_number} in {bcolors.OKBLUE}browser{bcolors.ENDC}!", flush=True)
			exec(['gh', 'pr', 'view', '--web', '-R', pr_repo, str(pr_number)])

	print("Quitting..")

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
