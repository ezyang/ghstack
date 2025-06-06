#!/usr/bin/env python3

import json
import logging
import re
import time
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import requests

import ghstack.github

MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 60


class RealGitHubEndpoint(ghstack.github.GitHubEndpoint):
    """
    A class representing a GitHub endpoint we can send queries to.
    It supports both GraphQL and REST interfaces.
    """

    # The URL of the GraphQL endpoint to connect to
    @property
    def graphql_endpoint(self) -> str:
        if self.github_url == "github.com":
            return f"https://api.{self.github_url}/graphql"
        else:
            return f"https://{self.github_url}/api/graphql"

    # The base URL of the REST endpoint to connect to (all REST requests
    # will be subpaths of this URL)
    @property
    def rest_endpoint(self) -> str:
        if self.github_url == "github.com":
            return f"https://api.{self.github_url}"
        else:
            return f"https://{self.github_url}/api/v3"

    # The base URL of regular WWW website, in case we need to manually
    # interact with the real website
    www_endpoint: str = "https://{github_url}"

    # The string OAuth token to authenticate to the GraphQL server with.
    # May be None if we're doing public access only.
    oauth_token: Optional[str]

    # The URL of a proxy to use for these connections (for
    # Facebook users, this is typically 'http://fwdproxy:8080')
    proxy: Optional[str]

    # The certificate bundle to be used to verify the connection.
    # Passed to requests as 'verify'.
    verify: Optional[str]

    # Client side certificate to use when connecitng.
    # Passed to requests as 'cert'.
    cert: Optional[Union[str, Tuple[str, str]]]

    def __init__(
        self,
        oauth_token: Optional[str],
        github_url: str,
        proxy: Optional[str] = None,
        verify: Optional[str] = None,
        cert: Optional[Union[str, Tuple[str, str]]] = None,
    ):
        self.oauth_token = oauth_token
        self.proxy = proxy
        self.github_url = github_url
        self.verify = verify
        self.cert = cert

    def push_hook(self, refName: Sequence[str]) -> None:
        pass

    def graphql(self, query: str, **kwargs: Any) -> Any:
        headers = {}
        if self.oauth_token:
            headers["Authorization"] = "bearer {}".format(self.oauth_token)

        logging.debug(
            "# POST {}".format(self.graphql_endpoint.format(github_url=self.github_url))
        )
        logging.debug("Request GraphQL query:\n{}".format(query))
        logging.debug(
            "Request GraphQL variables:\n{}".format(json.dumps(kwargs, indent=1))
        )

        resp = requests.post(
            self.graphql_endpoint.format(github_url=self.github_url),
            json={"query": query, "variables": kwargs},
            headers=headers,
            proxies=self._proxies(),
            verify=self.verify,
            cert=self.cert,
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

        if "errors" in r:
            raise RuntimeError(pretty_json)

        return r

    def _proxies(self) -> Dict[str, str]:
        if self.proxy:
            return {"http": self.proxy, "https": self.proxy}
        else:
            return {}

    def get_head_ref(self, **params: Any) -> str:

        if self.oauth_token:
            return super().get_head_ref(**params)
        else:
            owner = params["owner"]
            name = params["name"]
            number = params["number"]
            resp = requests.get(
                f"{self.www_endpoint.format(github_url=self.github_url)}/{owner}/{name}/pull/{number}",
                proxies=self._proxies(),
                verify=self.verify,
                cert=self.cert,
            )
            logging.debug("Response status: {}".format(resp.status_code))

            r = resp.text
            if m := re.search(r'<clipboard-copy.+?value="(gh/[^/]+/\d+/head)"', r):
                return m.group(1)
            else:
                # couldn't find, fall back to regular query
                return super().get_head_ref(**params)

    def rest(self, method: str, path: str, **kwargs: Any) -> Any:
        assert self.oauth_token
        headers = {
            "Authorization": "token " + self.oauth_token,
            "Content-Type": "application/json",
            "User-Agent": "ghstack",
            "Accept": "application/vnd.github.v3+json",
        }

        url = self.rest_endpoint.format(github_url=self.github_url) + "/" + path

        backoff_seconds = INITIAL_BACKOFF_SECONDS
        for attempt in range(0, MAX_RETRIES):
            logging.debug("# {} {}".format(method, url))
            logging.debug("Request body:\n{}".format(json.dumps(kwargs, indent=1)))

            resp: requests.Response = getattr(requests, method)(
                url,
                json=kwargs,
                headers=headers,
                proxies=self._proxies(),
                verify=self.verify,
                cert=self.cert,
            )

            logging.debug("Response status: {}".format(resp.status_code))

            try:
                r = resp.json()
            except ValueError:
                logging.debug("Response body:\n{}".format(r.text))
                raise
            else:
                pretty_json = json.dumps(r, indent=1)
                logging.debug("Response JSON:\n{}".format(pretty_json))

            # Per Github rate limiting: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api?apiVersion=2022-11-28#exceeding-the-rate-limit
            if resp.status_code in (403, 429):
                remaining_count = resp.headers.get("x-ratelimit-remaining")
                reset_time = resp.headers.get("x-ratelimit-reset")

                if remaining_count == "0" and reset_time:
                    sleep_time = int(reset_time) - int(time.time())
                    logging.warning(
                        f"Rate limit exceeded. Sleeping until reset in {sleep_time} seconds."
                    )
                    time.sleep(sleep_time)
                    continue
                else:
                    retry_after_seconds = resp.headers.get("retry-after")
                    if retry_after_seconds:
                        sleep_time = int(retry_after_seconds)
                        logging.warning(
                            f"Secondary rate limit hit. Sleeping for {sleep_time} seconds."
                        )
                    else:
                        sleep_time = backoff_seconds
                        logging.warning(
                            f"Secondary rate limit hit. Sleeping for {sleep_time} seconds (exponential backoff)."
                        )
                        backoff_seconds *= 2
                    time.sleep(sleep_time)
                    continue

            if resp.status_code == 404:
                raise ghstack.github.NotFoundError(
                    """\
GitHub raised a 404 error on the request for
{url}.
Usually, this doesn't actually mean the page doesn't exist; instead, it
usually means that you didn't configure your OAuth token with enough
permissions.  Please create a new OAuth token at
https://{github_url}/settings/tokens and DOUBLE CHECK that you checked
"public_repo" for permissions, and update ~/.ghstackrc with your new
value.

Another possible reason for this error is if the repository has moved
to a new location or been renamed. Check that the repository URL is
still correct.
""".format(
                        url=url, github_url=self.github_url
                    )
                )

            try:
                resp.raise_for_status()
            except requests.HTTPError:
                raise RuntimeError(pretty_json)

            return r

        raise RuntimeError("Exceeded maximum retries due to GitHub rate limiting")
