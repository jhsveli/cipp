import os
import json
import subprocess

def exec_json(cmd):
	string = exec(cmd)
	return json.loads(string)

def exec(cmd):	
	my_env = os.environ.copy()
	my_env['PAGER'] = 'cat'
	result = subprocess.run(cmd, capture_output=True, text=True, env=my_env, encoding='utf-8')	
	return result.stdout