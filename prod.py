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

def satisfies_criteria(pr):
	search_content = pr['title'] + pr['body']	
	
	return github_name in search_content or github_username in search_content or config_name in search_content or 'dependabot' in pr['body']


prs_raw = exec_json(['gh', 'pr', 'list', '-R', REPO, '-l', LABEL, '-S', 'prod in:title review-requested:@me', '--json', 'number,author,title,updatedAt,body'])

# Filter based on handle or name mentioned in pr title
prs = {str(pr['number']): pr for pr in prs_raw if satisfies_criteria(pr)}

if len(prs.keys()) == 0:
	print(f"0 waiting prod prs found for {github_username} or {github_name}")
	quit()


menuchoices = [f"{prs[key]['number']} {prs[key]['title']}" for key in prs.keys()]
menu = TerminalMenu(
	menuchoices,
	title = "Pick an image update in prod for approval:",	
	)

selected_index = menu.show()

if selected_index is not None and selected_index >= 0:
	pr_to_merge = prs[menuchoices[selected_index].split()[0]]
	print(f"Approving and merging '{pr_to_merge['number']}'...", end="")
	exec(['gh', 'pr', 'review', '--approve', str(pr_to_merge['number']), '-R', REPO])
	print("approved...merging...", end="" )
	exec(['gh', 'pr', 'merge', '-m', '--auto', '-R', REPO, str(pr_to_merge['number'])])
	print("Done!")
else:
	print("Quitting..")
