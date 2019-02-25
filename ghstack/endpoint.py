import json
import requests


class GraphQLEndpoint(object):
    """
    A class representing a GraphQL endpoint we can send queries to.
    At the moment, specifically engineered for GitHub.
    """

    def __init__(self, endpoint, future=False):
        """
        Args:
            endpoint: URL of the endpoint in question
        """
        self.endpoint = endpoint
        # String OAuth token for authenticating to GitHub
        self.oauth_token = None
        # String proxy to use for http and https requests
        self.proxy = None
        # Whether or not this API lives "in the future".  Features in
        # the future don't exist on the real GitHub API.
        self.future = future

    def graphql(self, query, **kwargs):
        """
        Args:
            query: string GraphQL query to execute
            **kwargs: values for variables in the graphql query
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


class RESTEndpoint(object):
    """
    A class representing a REST endpoint we can send queries to.
    At the moment, specifically engineered for GitHub.
    """

    def __init__(self, endpoint):
        self.endpoint = endpoint
        # String OAuth token for authenticating to GitHub
        self.oauth_token = None
        # String proxy to use for http and https requests
        self.proxy = None

    def _headers(self):
        return {
          'Authorization': 'token ' + self.oauth_token,
          'Content-Type': 'application/json',
          'User-Agent': 'ghstack',
          'Accept': 'application/vnd.github.v3+json',
          }

    def get(self, path, **kwargs):
        """
        Send a GET request to endpoint 'path'.
        """
        return self.rest('get', path, **kwargs)

    def post(self, path, **kwargs):
        """
        Send a POST request to endpoint 'path'.
        """
        return self.rest('post', path, **kwargs)

    def patch(self, path, **kwargs):
        """
        Send a PATCH request to endpoint 'path'.
        """
        return self.rest('patch', path, **kwargs)

    def rest(self, method, path, **kwargs):
        """
        Send a 'method' request to endpoint 'path'.

        Args:
            method: 'GET', 'POST', etc.
            path: relative URL path to access on endpoint
            **kwargs: dictionary of JSON payload to send
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
