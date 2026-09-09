import os
import json
import subprocess
import time

def exec_json(cmd):
	string = exec(cmd)
	return json.loads(string)

def exec(cmd, check=True):
	# check=True: a non-zero exit raises, surfacing stderr — otherwise a failed
	# `gh` call (e.g. a disallowed merge) returns "" and looks like success, so
	# the action silently "succeeds" and the row wrongly disappears.
	my_env = os.environ.copy()
	my_env['PAGER'] = 'cat'
	result = subprocess.run(cmd, capture_output=True, text=True, env=my_env, encoding='utf-8')
	if check and result.returncode != 0:
		detail = (result.stderr or result.stdout or "").strip()
		raise RuntimeError(detail or f"command failed ({result.returncode}): {' '.join(cmd)}")
	return result.stdout

def merge_pr(repo, number):
	# Prefer a rebase merge; fall back to squash if the repo disallows rebase.
	# Repos vary in which methods they permit (e.g. some allow only rebase, others
	# only squash), so trying one and falling back covers both without querying.
	cmd = ['gh', 'pr', 'merge', '-r', '-R', repo, str(number)]
	for attempt in range(3):
		try:
			return exec(cmd)
		except RuntimeError as e:
			# "Base branch was modified" is GitHub's transient mergeability race,
			# not a disallowed-method error — retrying the same call (not squash)
			# resolves it once the base settles.
			if 'base branch was modified' in str(e).lower() and attempt < 2:
				time.sleep(1.5)
				continue
			break
	return exec(['gh', 'pr', 'merge', '-s', '-R', repo, str(number)])

def exec_input(cmd, text):
	# Like exec, but feeds `text` to the command's stdin (e.g. piping to pbcopy).
	my_env = os.environ.copy()
	my_env['PAGER'] = 'cat'
	subprocess.run(cmd, input=text, capture_output=True, text=True, env=my_env, encoding='utf-8')