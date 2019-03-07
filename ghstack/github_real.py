#!/usr/bin/env python3

import json
import requests
from typing import Optional, Any, Sequence

import ghstack.github


class RealGitHubEndpoint(ghstack.github.GitHubEndpoint):
    """
    A class representing a GitHub endpoint we can send queries to.
    It supports both GraphQL and REST interfaces.
    """

    # The URL of the GraphQL endpoint to connect to
    graphql_endpoint: str = 'https://api.github.com/graphql'

    # The base URL of the REST endpoint to connect to (all REST requests
    # will be subpaths of this URL)
    rest_endpoint: str = 'https://api.github.com'

    # The string OAuth token to authenticate to the GraphQL server with
    oauth_token: str

    # The URL of a proxy to use for these connections (for
    # Facebook users, this is typically 'http://fwdproxy:8080')
    proxy: Optional[str]

    def __init__(self,
                 oauth_token: str,
                 proxy: Optional[str] = None):
        """
        Args:
            endpoint: URL of the endpoint in question
        """
        self.oauth_token = oauth_token
        self.proxy = proxy

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

        resp = requests.post(
            self.graphql_endpoint,
            json={"query": query, "variables": kwargs},
            headers=headers,
            proxies=proxies
        )

        # Actually, this code is dead on the GitHub GraphQL API, because
        # they seem to always return 200, even in error case (as of
        # 11/5/2018)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            raise RuntimeError(json.dumps(resp.json(), indent=1))

        r = resp.json()

        if 'errors' in r:
            raise RuntimeError(json.dumps(r, indent=1))

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

        r = getattr(requests, method)(self.rest_endpoint + '/' + path,
                                      json=kwargs,
                                      headers=headers,
                                      proxies=proxies)
        r.raise_for_status()

        return r.json()
