#!/usr/bin/env python3

from abc import ABCMeta, abstractmethod
from typing import Any, Sequence

import ghstack.diff


class NotFoundError(RuntimeError):
    pass


class GitHubEndpoint(metaclass=ABCMeta):
    @abstractmethod
    async def graphql(self, query: str, **kwargs: Any) -> Any:
        """
        Args:
            query: string GraphQL query to execute
            **kwargs: values for variables in the graphql query

        Returns: parsed JSON response
        """
        pass

    async def get_head_ref(self, **params: Any) -> str:
        """
        Fetch the headRefName associated with a PR.  Defaults to a
        GraphQL query but if we're hitting a real GitHub endpoint
        we'll do a regular HTTP request to avoid rate limit.
        """
        pr_result = await self.graphql(
            """
            query ($owner: String!, $name: String!, $number: Int!) {
                repository(name: $name, owner: $owner) {
                    pullRequest(number: $number) {
                        headRefName
                    }
                }
            }
        """,
            **params,
        )
        r = pr_result["data"]["repository"]["pullRequest"]["headRefName"]
        assert isinstance(r, str), type(r)
        return r

    # This hook function should be invoked when a 'git push' to GitHub
    # occurs.  This is used by testing to simulate actions GitHub
    # takes upon branch push, more conveniently than setting up
    # a branch hook on the repository and receiving events from it.
    # TODO: generalize to any repo
    @abstractmethod
    def push_hook(self, refName: Sequence[str]) -> None:
        pass

    # This should be subsumed by push_hook above, but push_hook is
    # annoying to implement and this is more direct
    def notify_merged(self, pr_resolved: ghstack.diff.PullRequestResolved) -> None:
        pass

    async def aget(self, path: str, **kwargs: Any) -> Any:
        return await self.arest("get", path, **kwargs)

    async def apost(self, path: str, **kwargs: Any) -> Any:
        return await self.arest("post", path, **kwargs)

    async def apatch(self, path: str, **kwargs: Any) -> Any:
        return await self.arest("patch", path, **kwargs)

    @abstractmethod
    async def arest(self, method: str, path: str, **kwargs: Any) -> Any:
        """
        Send an async 'method' request to endpoint 'path'.

        Args:
            method: 'GET', 'POST', etc.
            path: relative URL path to access on endpoint
            **kwargs: dictionary of JSON payload to send

        Returns: parsed JSON response
        """
        pass
