import json
import os

from .cmd import exec

# Posting to Slack is gated on both env vars being present. When either is
# unset, the feature (and its action key) simply does not exist.
USER_TOKEN = os.environ.get('CIPP_SLACK_MESSAGE_USER_TOKEN')
CHANNEL_ID = os.environ.get('CIPP_SLACK_MESSAGE_CHANNEL_ID')


def enabled() -> bool:
	return bool(USER_TOKEN and CHANNEL_ID)


def post_message(text: str) -> None:
	# Posts as the user (xoxp- token) into the configured channel. The payload
	# is json.dumps'd so PR titles with quotes/backticks can't break the body;
	# passing it as a list arg to curl avoids any shell-escaping concerns.
	payload = json.dumps({'channel': CHANNEL_ID, 'text': text})
	raw = exec([
		'curl', '-s', '-X', 'POST', 'https://slack.com/api/chat.postMessage',
		'-H', f'Authorization: Bearer {USER_TOKEN}',
		'-H', 'Content-Type: application/json; charset=utf-8',
		'-d', payload,
	])
	try:
		result = json.loads(raw)
	except (json.JSONDecodeError, ValueError):
		raise RuntimeError(f"Slack: unexpected response: {raw[:120]}")
	if not result.get('ok'):
		raise RuntimeError(f"Slack: {result.get('error', 'unknown error')}")
