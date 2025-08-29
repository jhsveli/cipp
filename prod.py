import sys

from simple_term_menu import TerminalMenu
from cmd import exec, exec_json

REPO = "sparebank1utvikling/app-configrepo-sb1u"
LABEL = "image-updater"

exec(['gh', 'config', 'set', 'pager', 'cat'])
github_user = exec_json(['gh', 'api', 'user', '--jq', '{name,login}'])
config_name = exec(['git', 'config', '--global', '--get', 'user.name']).strip()
github_username = github_user['login']
github_name = github_user['name']

def prod_menu():
	def satisfies_criteria(pr):
		search_content = pr['title'] + pr['body']	
		
		return github_name in search_content or github_username in search_content or config_name in search_content or 'dependabot' in pr['body']


	prs_raw = exec_json(['gh', 'pr', 'list', '-R', REPO, '-l', LABEL, '-S', 'prod in:title review-requested:@me', '--json', 'id,number,author,title,updatedAt,body'])

	# Filter based on handle or name mentioned in pr title
	prs = {str(pr['id']): pr for pr in prs_raw if satisfies_criteria(pr)}

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
		exec(['gh', 'pr', 'merge', '-m', '--auto', '-R', REPO, str(pr_to_merge['number'])])
		print("Done!\n")
		approved_prs.add(pr_to_merge['id'])
	else:
		print("Quitting..")

if __name__ == '__main__':
	prod_menu()