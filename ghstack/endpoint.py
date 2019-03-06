#!/usr/bin/env python3

import json
import requests
from typing import Optional, Any, Sequence


class GitHubEndpoint(object):
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

    # Whether or not this GitHub endpoint supports features on GraphQL
    # that don't exist on real GitHub
    future: bool

    def __init__(self,
                 oauth_token: str,
                 proxy: Optional[str] = None,
                 future: bool = False):
        """
        Args:
            endpoint: URL of the endpoint in question
        """
        self.oauth_token = oauth_token
        self.proxy = proxy
        self.future = future

    def graphql(self, query: str, **kwargs: Any) -> Any:
        """
        Args:
            query: string GraphQL query to execute
            **kwargs: values for variables in the graphql query

        Returns: parsed JSON response
        """
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

    # This hook function should be invoked when a 'git push' to GitHub
    # occurs.  This is used by testing to simulate actions GitHub
    # takes upon branch push, more conveniently than setting up
    # a branch hook on the repository and receiving events from it.
    # TODO: generalize to any repo
    def push_hook(self, refName: Sequence[str]) -> None:
        pass

    def get(self, path: str, **kwargs: Any) -> Any:
        """
        Send a GET request to endpoint 'path'.

        Returns: parsed JSON response
        """
        return self.rest('get', path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        """
        Send a POST request to endpoint 'path'.

        Returns: parsed JSON response
        """
        return self.rest('post', path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        """
        Send a PATCH request to endpoint 'path'.

        Returns: parsed JSON response
        """
        return self.rest('patch', path, **kwargs)

    def rest(self, method: str, path: str, **kwargs: Any) -> Any:
        """
        Send a 'method' request to endpoint 'path'.

        Args:
            method: 'GET', 'POST', etc.
            path: relative URL path to access on endpoint
            **kwargs: dictionary of JSON payload to send

        Returns: parsed JSON response
        """
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
