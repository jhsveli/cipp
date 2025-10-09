from cmd import exec, exec_json

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
	github_user = exec_json(['gh', 'api', 'user', '--jq', '{name,login}'])
	github_username = github_user['login']
	github_name = github_user['name']

	print("Finding open prs with 🟢 checks..\n")
	prs_raw = exec_json(['gh', 'search', 'prs', '--json', 'repository,id,number,title,author,createdAt', '--state=open', '--review-requested=@me', '--checks=success', '--sort=updated', '--', '-label:image-updater'])


	prs = {str(pr['id']): pr for pr in prs_raw}

	if len(prs.keys()) == 0:
		print(f"{bcolors.WARNING}0 waiting prs{bcolors.ENDC} found for {github_username} or {github_name}")
		quit()
	else:
		print(f"Found {bcolors.OKBLUE}{len(prs.keys())}{bcolors.ENDC} prs. Select a pr to {bcolors.OKGREEN}[a]pprove{bcolors.ENDC} and/or {bcolors.OKCYAN}[m]erge{bcolors.ENDC}:")

	def render_statusbar(menuitem):	
		pr = prs[menuitems_to_id[menuitem]]
		emoji = '🤖' if pr['author']['type'] == 'Bot' else '🧬'
		return f"In: {pr['repository']['nameWithOwner']} | By: {emoji} {pr['author']['login']} | On: {pr['createdAt']}"

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
			accept_keys = ('enter', 'a', 'm'),
			status_bar = render_statusbar
			)
		selected_index = menu.show()

		if selected_index is None or selected_index < 0:
			break

		accept_key_pressed = menu.chosen_accept_key
		pr_to_merge = prs[menuitems_to_id[menuchoices[selected_index]]]
		
		pr_number = pr_to_merge['number']
		pr_repo = pr_to_merge['repository']['nameWithOwner']

		
		print(f"Approving '{pr_to_merge['number']}'... ", end="", flush=True)
		exec(['gh', 'pr', 'review', '--approve', str(pr_number), '-R', pr_repo])
			
		if accept_key_pressed == 'm':
			print(f"{bcolors.OKGREEN}Approved{bcolors.ENDC}! Now merging... ", end="", flush=True)
			exec(['gh', 'pr', 'merge', '-s', '-R', pr_repo, str(pr_number)])
			print(f"{bcolors.OKCYAN}Merged{bcolors.ENDC}!\n")
		else:
			print(f"{bcolors.OKGREEN}Approved{bcolors.ENDC}!\n")

		approved_prs.add(pr_to_merge['id'])

	print("Quitting..")


if __name__ == '__main__':
	pr_menu()
