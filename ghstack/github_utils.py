#!/usr/bin/env python3

import re
from typing import Optional

from typing_extensions import TypedDict

import ghstack.github
import ghstack.shell
from ghstack.types import GitHubRepositoryId

GitHubRepoNameWithOwner = TypedDict('GitHubRepoNameWithOwner', {
    'owner': str,
    'name': str,
})


def get_github_repo_name_with_owner(
    *,
    sh: ghstack.shell.Shell,
    github_url: str,
    remote_name: str,
) -> GitHubRepoNameWithOwner:
    # Grovel in remotes to figure it out
    remote_url = sh.git("remote", "get-url", remote_name)
    while True:
        match = r'^git@{github_url}:([^/]+)/([^.]+)(?:\.git)?$'.format(
            github_url=github_url
        )
        m = re.match(match, remote_url)
        if m:
            owner = m.group(1)
            name = m.group(2)
            break
        search = r'{github_url}/([^/]+)/([^.]+)'.format(
            github_url=github_url
        )
        m = re.search(search, remote_url)
        if m:
            owner = m.group(1)
            name = m.group(2)
            break
        raise RuntimeError(
            "Couldn't determine repo owner and name from url: {}"
            .format(remote_url))
    return {'owner': owner, 'name': name}


GitHubRepoInfo = TypedDict('GitHubRepoInfo', {
    'name_with_owner': GitHubRepoNameWithOwner,
    'id': GitHubRepositoryId,
    'is_fork': bool,
    'default_branch': str,
})


def get_github_repo_info(
    *,
    github: ghstack.github.GitHubEndpoint,
    sh: ghstack.shell.Shell,
    repo_owner: Optional[str] = None,
    repo_name: Optional[str] = None,
    github_url: str,
    remote_name: str,
) -> GitHubRepoInfo:
    if repo_owner is None or repo_name is None:
        name_with_owner = get_github_repo_name_with_owner(
            sh=sh,
            github_url=github_url,
            remote_name=remote_name,
        )
    else:
        name_with_owner = {"owner": repo_owner, "name": repo_name}

    # TODO: Cache this guy
    repo = github.graphql(
        """
        query ($owner: String!, $name: String!) {
            repository(name: $name, owner: $owner) {
                id
                isFork
                defaultBranchRef {
                    name
                }
            }
        }""",
        owner=name_with_owner["owner"],
        name=name_with_owner["name"])["data"]["repository"]

    return {
        "name_with_owner": name_with_owner,
        "id": repo["id"],
        "is_fork": repo["isFork"],
        "default_branch": repo["defaultBranchRef"]["name"],
    }


RE_PR_URL = re.compile(
    r'^https://(?P<github_url>[^/]+)/(?P<owner>[^/]+)/(?P<name>[^/]+)/pull/(?P<number>[0-9]+)/?$')

GitHubPullRequestParams = TypedDict('GitHubPullRequestParams', {
    'github_url': str,
    'owner': str,
    'name': str,
    'number': int,
})


def parse_pull_request(pull_request: str) -> GitHubPullRequestParams:
    m = RE_PR_URL.match(pull_request)
    if not m:
        raise RuntimeError("Did not understand PR argument.  PR must be URL")

    github_url = m.group("github_url")
    owner = m.group("owner")
    name = m.group("name")
    number = int(m.group("number"))
    return {'github_url': github_url, 'owner': owner, 'name': name, 'number': number}
