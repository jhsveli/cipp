import json
import os
import subprocess
import sys

from simple_term_menu import TerminalMenu

def exec_json(cmd):
	string = exec(cmd)
	return json.loads(string)

def exec(cmd):	
	my_env = os.environ.copy()
	my_env['PAGER'] = 'cat'
	result = subprocess.run(cmd, capture_output=True, text=True, env=my_env, encoding='utf-8')	
	return result.stdout

exec(['gh', 'config', 'set', 'pager', 'cat'])

REPO = "sparebank1utvikling/app-configrepo-sb1u"
LABEL = "image-updater"

github_user = exec_json(['gh', 'api', 'user', '--jq', '{name,login}'])
github_username = github_user['login']
github_name = github_user['name']

prs_raw = exec_json(['gh', 'pr', 'list', '-R', REPO, '-l', LABEL, '-S', 'prod in:title review-requested:@me', '--json', 'number,title,updatedAt'])

# Filter based on handle or name mentioned in pr title
prs = {str(pr['number']): pr for pr in prs_raw if github_name in pr['title'] or github_username in pr['title']}

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
	exec(['gh', 'pr', 'merge', '-m', '-R', REPO, str(pr_to_merge['number'])])
	print("Done!")
else:
	print("Quitting..")
