#!/usr/bin/env python3

import json
import requests
from typing import Optional, Dict, Any, List


class GraphQLEndpoint(object):
    """
    A class representing a GraphQL endpoint we can send queries to.
    At the moment, specifically engineered for GitHub.
    """

    # The URL of the endpoint to connect to
    endpoint: str

    # The string OAuth token to authenticate to the GraphQL server with
    oauth_token: str

    # The URL of a proxy to use for these connections (for
    # Facebook users, this is typically http://fwdproxy:8080)
    proxy: Optional[str]

    # Whether or not this API lives "in the future".  Features in
    # the future don't exist on the real GitHub API.
    future: bool

    def __init__(self,
                 endpoint: str,
                 oauth_token: str,
                 proxy: Optional[str] = None,
                 future: bool = False):
        """
        Args:
            endpoint: URL of the endpoint in question
        """
        self.endpoint = endpoint
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
            self.endpoint,
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

    # Call this whenever you do push
    # TODO: generalize to any repo
    def push_hook(self, refName: List[str]) -> None:
        pass


class RESTEndpoint(object):
    """
    A class representing a REST endpoint we can send queries to.
    At the moment, specifically engineered for GitHub.
    """

    # The base URL of the endpoint to connect to (all requests will
    # be subpaths of this URL)
    endpoint: str

    # String OAuth token for authenticating to GitHub
    oauth_token: str

    # String proxy to use for http and https requests
    proxy: Optional[str]

    def __init__(self, endpoint: str, oauth_token: str,
                 proxy: Optional[str] = None):
        self.endpoint = endpoint
        self.oauth_token = oauth_token
        self.proxy = proxy

    def _headers(self) -> Dict[str, str]:
        return {
            'Authorization': 'token ' + self.oauth_token,
            'Content-Type': 'application/json',
            'User-Agent': 'ghstack',
            'Accept': 'application/vnd.github.v3+json',
        }

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
        r = getattr(requests, method)(self.endpoint + '/' + path,
                                      json=kwargs,
                                      headers=self._headers(),
                                      proxies=proxies)
        r.raise_for_status()
        return r.json()
