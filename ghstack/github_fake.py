import asyncio
import graphql
from dataclasses import dataclass

from typing import Dict, NewType, List, Optional, Any
try:
    from mypy_extensions import TypedDict
except ImportError:
    # Avoid the dependency on the mypy_extensions package.
    # It is required, however, for type checking.
    def TypedDict(name, attrs, total=True):  # type: ignore
        return Dict[Any, Any]

GraphQLId = NewType('GraphQLId', int)
GitHubNumber = NewType('GitHubNumber', int)

UpdatePullRequestInput = TypedDict('UpdatePullRequestInput', {
    'baseRefName': Optional[str],
    'title': Optional[str],
    'body': Optional[str],

    'clientMutationId': Optional[str],
    'pullRequestId': GraphQLId,
})

CreatePullRequestInput = TypedDict('CreatePullRequestInput', {
    'baseRefName': str,
    'headRefName': str,
    'title': str,
    'body': str,

    'clientMutationId': Optional[str],
    'ownerId': GraphQLId,
})

class Repository:
    ...

class PullRequest:
    ...

class Root:
    ...

# The "database" for our mock instance
class GitHubState:
    repositories: Dict[GraphQLId, Repository]
    pull_requests: Dict[GraphQLId, PullRequest]
    next_id: GraphQLId
    next_pull_request_number: Dict[GraphQLId, GitHubNumber]
    root: Root

    def __init__(self) -> None:
        self.repositories = {}
        self.pull_requests = {}
        self.next_id = GraphQLId(5000)
        self.next_pull_request_number = {}
        self.root = Root()

        # Populate it with the most important repo ;)
        self.repositories[GraphQLId(1000)] = Repository(
            id=GraphQLId(1000),
            name="pytorch",
            nameWithOwner="pytorch/pytorch",
        )
        self.next_pull_request_number[GraphQLId(1000)] = GitHubNumber(500)

@dataclass
class Node:
    id: GraphQLId

@dataclass
class PullRequestConnection:
    nodes: List[PullRequest]

@dataclass
class Repository(Node):
    name: str
    nameWithOwner: str

    def pullRequest(self, info: GitHubState, number: GitHubNumber) -> PullRequest:
        for pr in info.pull_requests.values():
            if self.id == pr.repository and pr.number == number:
                return pr
        raise RuntimeError(
            "unrecognized pull request #{} in repository {}"
            .format(number, self.nameWithOwner))

    def pullRequests(self, info: GitHubState) -> PullRequestConnection:
        return PullRequestConnection(
                nodes=list(filter(lambda pr: self.id == pr.repository, info.pull_requests.values())))

@dataclass
class PullRequest(Node):
    # baseRef: Optional[Ref]
    baseRefName: str
    body: str
    # closed: bool
    # headRef: Optional[Ref]
    headRefName: str
    # headRepository: Optional[Repository]
    # maintainerCanModify: bool
    number: GitHubNumber
    _repository: GraphQLId  # cycle breaker
    # state: PullRequestState
    title: str
    url: str

    def repository(self, info: GitHubState) -> Repository:
        return info.repositories[self._repository]

@dataclass
class UpdatePullRequestPayload:
    clientMutationId: Optional[str]
    pullRequest: PullRequest

@dataclass
class CreatePullRequestPayload:
    clientMutationId: Optional[str]
    pullRequest: PullRequest

class Root:
    def repository(self, info: GitHubState, owner: str, name: str) -> Repository:
        nameWithOwner = "{}/{}".format(owner, name)
        for r in info.repositories.values():
            if r.nameWithOwner == nameWithOwner:
                return r
        raise RuntimeError("unknown repository {}".format(nameWithOwner))

    def node(self, info: GitHubState, id: GraphQLId) -> Node:
        if id in info.repositories:
            return info.repositories[id]
        elif id in info.pull_requests:
            return info.pull_requests[id]
        else:
            raise RuntimeError("unknown id {}".format(id))

    def updatePullRequest(self,
                          info: GitHubState,
                          input: UpdatePullRequestInput
                          ) -> UpdatePullRequestPayload:
        pr = info.pull_requests[input['pullRequestId']]
        if input['title'] is not None:
            pr.title = input['title']
        if input['baseRefName'] is not None:
            pr.baseRefName = input['baseRefName']
        if input['body'] is not None:
            pr.body = input['body']
        return UpdatePullRequestPayload(
                clientMutationId=input['clientMutationId'],
                pullRequest=pr)

    def createPullRequest(self,
                          info: GitHubState,
                          input: CreatePullRequestInput
                          ) -> CreatePullRequestPayload:
        id = info.next_id
        info.next_id = GraphQLId(info.next_id + 1)
        repo = info.repositories[input['ownerId']]
        number = info.next_pull_request_number[input['ownerId']]
        info.next_pull_request_number[input['ownerId']] = \
                GitHubNumber(info.next_pull_request_number[input['ownerId']] + 1)
        pr = PullRequest(
            id=id,
            _repository=input['ownerId'],
            number=number,
            url="https://github.com/{}/pull/{}".format(repo.nameWithOwner, number),
            baseRefName=input['baseRefName'],
            headRefName=input['headRefName'],
            title=input['title'],
            body=input['body'],
        )
        info.pull_requests[id] = pr
        return CreatePullRequestPayload(
                clientMutationId=input['clientMutationId'],
                pullRequest=pr)

with open('github-fake/src/schema.graphql') as f:
    GITHUB_SCHEMA = graphql.build_schema(f.read())

class FakeGitHubGraphQLEndpoint(object):
    def __init__(self):
        self.info = GitHubState()

    def graphql(self, query: str, **kwargs: Any) -> Any:
        r = graphql.graphql_sync(GITHUB_SCHEMA, query, self.info.root, variable_values=kwargs)
        if r.errors:
            raise RuntimeError("GraphQL query failed with errors:\n\n{}"
                               .format("\n".join(str(e) for e in r.errors)))
        return r.data
