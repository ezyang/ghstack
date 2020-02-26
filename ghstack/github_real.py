#!/usr/bin/env python3

import json
import requests
from typing import Optional, Any, Sequence
import logging

import ghstack.github


class RealGitHubEndpoint(ghstack.github.GitHubEndpoint):
    """
    A class representing a GitHub endpoint we can send queries to.
    It supports both GraphQL and REST interfaces.
    """

    # The URL of the GraphQL endpoint to connect to
    graphql_endpoint: str = 'https://api.{github_url}/graphql'

    # The base URL of the REST endpoint to connect to (all REST requests
    # will be subpaths of this URL)
    rest_endpoint: str = 'https://api.{github_url}'

    # The string OAuth token to authenticate to the GraphQL server with
    oauth_token: str

    # The URL of a proxy to use for these connections (for
    # Facebook users, this is typically 'http://fwdproxy:8080')
    proxy: Optional[str]

    def __init__(self,
                 oauth_token: str,
                 github_url: str,
                 proxy: Optional[str] = None):
        self.oauth_token = oauth_token
        self.proxy = proxy
        self.github_url = github_url

    def push_hook(self, refName: Sequence[str]) -> None:
        pass

    def graphql(self, query: str, **kwargs: Any) -> Any:
        headers = {}
        if self.oauth_token:
            headers['Authorization'] = 'bearer {}'.format(self.oauth_token)

        if self.proxy:
            proxies = {
                'http': self.proxy,
                'https': self.proxy
            }
        else:
            proxies = {}

        logging.debug("# POST {}".format(self.graphql_endpoint.format(github_url=self.github_url)))
        logging.debug("Request GraphQL query:\n{}".format(query))
        logging.debug("Request GraphQL variables:\n{}"
                      .format(json.dumps(kwargs, indent=1)))

        resp = requests.post(
            self.graphql_endpoint.format(github_url=self.github_url),
            json={"query": query, "variables": kwargs},
            headers=headers,
            proxies=proxies
        )

        logging.debug("Response status: {}".format(resp.status_code))

        try:
            r = resp.json()
        except ValueError:
            logging.debug("Response body:\n{}".format(resp.text))
            raise
        else:
            pretty_json = json.dumps(r, indent=1)
            logging.debug("Response JSON:\n{}".format(pretty_json))

        # Actually, this code is dead on the GitHub GraphQL API, because
        # they seem to always return 200, even in error case (as of
        # 11/5/2018)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            raise RuntimeError(pretty_json)

        if 'errors' in r:
            raise RuntimeError(pretty_json)

        return r

    def rest(self, method: str, path: str, **kwargs: Any) -> Any:
        if self.proxy:
            proxies = {
                'http': self.proxy,
                'https': self.proxy
            }
        else:
            proxies = {}

        headers = {
            'Authorization': 'token ' + self.oauth_token,
            'Content-Type': 'application/json',
            'User-Agent': 'ghstack',
            'Accept': 'application/vnd.github.v3+json',
        }

        url = self.rest_endpoint.format(github_url=self.github_url) + '/' + path
        logging.debug("# {} {}".format(method, url))
        logging.debug("Request body:\n{}".format(json.dumps(kwargs, indent=1)))

        resp: requests.Response = \
            getattr(requests, method)(url,
                                      json=kwargs,
                                      headers=headers,
                                      proxies=proxies)

        logging.debug("Response status: {}".format(resp.status_code))

        try:
            r = resp.json()
        except ValueError:
            logging.debug("Response body:\n{}".format(r.text))
            raise
        else:
            pretty_json = json.dumps(r, indent=1)
            logging.debug("Response JSON:\n{}".format(pretty_json))

        if resp.status_code == 404:
            raise RuntimeError("""\
GitHub raised a 404 error on the request for
{url}.
Usually, this doesn't actually mean the page doesn't exist; instead, it
usually means that you didn't configure your OAuth token with enough
permissions.  Please create a new OAuth token at
https://{github_url}/settings/tokens and DOUBLE CHECK that you checked
"public_repo" for permissions, and update ~/.ghstackrc with your new
value.
""".format(url=url, github_url=self.github_url))

        try:
            resp.raise_for_status()
        except requests.HTTPError:
            raise RuntimeError(pretty_json)

        return r
