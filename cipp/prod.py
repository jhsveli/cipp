from .abbreviate import short_repo
from .cmd import exec, exec_json
from .extract_author import extract_authors
from .pr_menu import ActionResult, ActionSpec, ColumnSpec, Safeguard, TabConfig, format_age

REPO = "sparebank1utvikling/app-configrepo-sb1u"
AUTHOR = "aws-plattform-image-updater"

# Sometimes, if git config name differs from github user full name,
# The PRs can be tagged with either names, must check for both.
# eg Jorgen Tu Sveli and Jørgen Tu Sveli
config_name = exec(['git', 'config', '--global', '--get', 'user.name']).strip()

# My identities, resolved once at load: local git name + the gh-authenticated
# login and full name. Used to tell my own image-update PRs from teammates'.
_gh_identity = exec_json(['gh', 'api', 'user', '--jq', '{login: .login, name: .name}'])
MY_IDENTITIES = {
	s.strip().casefold()
	for s in (config_name, _gh_identity.get('login'), _gh_identity.get('name'))
	if s and s.strip()
}


def needs_confirm(pr) -> bool:
	# Safeguard only a teammate's change. An image-update PR's GitHub author is
	# always the bot; the human who made the change is encoded in the title, so
	# match the resolved author against my identities. A resolved *bot* author is
	# never gated, nor is my own change.
	# Confirm if *any* resolved author is a teammate (non-bot, not me).
	return any(
		not is_bot and name.strip().casefold() not in MY_IDENTITIES
		for name, is_bot in extract_authors(pr)
	)


def post_process(slice):
	github_username = slice['user']['login']
	github_name = slice['user']['name']

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
		pr for pr in slice['prs']
		if satisfies_criteria(pr) and pr['checkStatus'] == 'SUCCESS'
	]


def approve_and_merge(pr):
	exec(['gh', 'pr', 'review', '--approve', str(pr['number']), '-R', REPO])
	exec(['gh', 'pr', 'merge', '-s', '-R', REPO, str(pr['number'])])
	return ActionResult.REMOVE


# Title format: "<app>: <env> - <timestamp-sha>: <orig title> (#num) (author)"
def app_name(pr):
	return short_repo(pr['title'].split(':', 1)[0].strip())


def short_title(pr):
	# Strip the "<app>: <env> - " boilerplate, keep "<timestamp-sha>: <orig title>...".
	title = pr['title']
	after_app = title.split(':', 1)[1] if ':' in title else title
	return after_app.split(' - ', 1)[1].strip() if ' - ' in after_app else title.strip()


def author_label(pr):
	authors = extract_authors(pr)
	name, is_bot = authors[0]
	emoji = '🤖' if is_bot else '🧠'
	label = f"{emoji} {name}"
	rest = authors[1:]
	if rest:
		icons = ""
		if any(b for _, b in rest):
			icons += "🤖"
		if any(not b for _, b in rest):
			icons += "🧠"
		label += f" + {len(rest)} {icons}"
	return label


SEARCH_QUERY = "type:pr state:open repo:sparebank1utvikling/app-configrepo-sb1u prod in:title review-requested:@me sort:created-desc"

NODE_SELECTION = """
			  url
			  id
			  number
			  title
			  createdAt
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
"""

JQ_PROJECTION = """
{
	user: {
		login: .data.viewer.login,
		name: .data.viewer.name
	},
	prs: [.data.prod.edges[].node | {
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

TAB = TabConfig(
	name="Production",
	title="Pick an image update in prod for approval",
	alias="prod",
	search_query=SEARCH_QUERY,
	node_selection=NODE_SELECTION,
	jq_projection=JQ_PROJECTION,
	post_process=post_process,
	actions=[
		ActionSpec(
			key="enter",
			label="Approve + merge",
			handler=approve_and_merge,
			safeguard=Safeguard(
				when=needs_confirm,
				descriptor="a teammate's",
			),
		),
	],
	status_bar=lambda pr: pr['body'],
	pr_label=short_title,
	idle_label=author_label,
	columns=[
		ColumnSpec(
			key="age",
			label="Age",
			width=6,
			render=lambda pr: format_age(pr["createdAt"]),
		),
		ColumnSpec(
			key="app",
			label="App",
			width=34,
			render=app_name,
		),
	],
)
