#!/usr/bin/env python3

# Only for now; see
# https://github.com/graphql-python/graphql-core-next/issues/26
import graphql  # type: ignore

import re
import os.path

# Oof! Python 3.7 only!!
from dataclasses import dataclass

from typing import Dict, NewType, List, Optional, Any, Sequence, cast
# TODO: do something better about this...
try:
    from mypy_extensions import TypedDict
except ImportError:
    # Avoid the dependency on the mypy_extensions package.
    # It is required, however, for type checking.
    def TypedDict(name, attrs, total=True):  # type: ignore
        return Dict[Any, Any]

import ghstack.shell
import ghstack.github

GraphQLId = NewType('GraphQLId', str)
GitHubNumber = NewType('GitHubNumber', int)
GitObjectID = NewType('GitObjectID', str)

UpdatePullRequestInput = TypedDict('UpdatePullRequestInput', {
    'base': Optional[str],
    'title': Optional[str],
    'body': Optional[str],
})

CreatePullRequestInput = TypedDict('CreatePullRequestInput', {
    'base': str,
    'head': str,
    'title': str,
    'body': str,
    'maintainer_can_modify': bool,
})

CreatePullRequestPayload = TypedDict('CreatePullRequestPayload', {
    'number': int,
})


# The "database" for our mock instance
class GitHubState:
    repositories: Dict[GraphQLId, 'Repository']
    pull_requests: Dict[GraphQLId, 'PullRequest']
    _next_id: int
    _next_pull_request_number: Dict[GraphQLId, int]
    root: 'Root'
    upstream_sh: Optional[ghstack.shell.Shell]

    def repository(self, owner: str, name: str) -> 'Repository':
        nameWithOwner = "{}/{}".format(owner, name)
        for r in self.repositories.values():
            if r.nameWithOwner == nameWithOwner:
                return r
        raise RuntimeError("unknown repository {}".format(nameWithOwner))

    def pull_request(self, repo: 'Repository', number: GitHubNumber
                     ) -> 'PullRequest':
        for pr in self.pull_requests.values():
            if repo.id == pr._repository and pr.number == number:
                return pr
        raise RuntimeError(
            "unrecognized pull request #{} in repository {}"
            .format(number, repo.nameWithOwner))

    def next_id(self) -> GraphQLId:
        r = GraphQLId(str(self._next_id))
        self._next_id += 1
        return r

    def next_pull_request_number(self, repo_id: GraphQLId) -> GitHubNumber:
        r = GitHubNumber(self._next_pull_request_number[repo_id])
        self._next_pull_request_number[repo_id] += 1
        return r

    def push_hook(self, refs: Sequence[str]) -> None:
        # updated_refs = set(refs)
        # for pr in self.pull_requests:
        #    # TODO: this assumes only origin repository
        #    # if pr.headRefName in updated_refs:
        #    #    pr.headRef =
        #    pass
        pass

    def __init__(self, upstream_sh: Optional[ghstack.shell.Shell]) -> None:
        self.repositories = {}
        self.pull_requests = {}
        self._next_id = 5000
        self._next_pull_request_number = {}
        self.root = Root()

        # Populate it with the most important repo ;)
        self.repositories[GraphQLId("1000")] = Repository(
            id=GraphQLId("1000"),
            name="pytorch",
            nameWithOwner="pytorch/pytorch",
            isFork=False,
        )
        self._next_pull_request_number[GraphQLId("1000")] = 500

        self.upstream_sh = upstream_sh
        if self.upstream_sh is not None:
            # Setup upstream Git repository representing the
            # pytorch/pytorch repository in the directory specified
            # by upstream_sh.  This is useful because some GitHub API
            # operations depend on repository state (e.g., what
            # the headRef is at the time a PR is created), so
            # we need this information
            self.upstream_sh.git("init", "--bare")
            tree = self.upstream_sh.git("write-tree")
            commit = self.upstream_sh.git(
                "commit-tree",
                tree,
                input="Initial commit")
            self.upstream_sh.git("branch", "-f", "master", commit)


@dataclass
class Node:
    id: GraphQLId


GraphQLResolveInfo = Any  # for now


def github_state(info: GraphQLResolveInfo) -> GitHubState:
    context = info.context
    assert isinstance(context, GitHubState)
    return context


@dataclass
class Repository(Node):
    name: str
    nameWithOwner: str
    isFork: bool

    def pullRequest(self,
                    info: GraphQLResolveInfo,
                    number: GitHubNumber) -> 'PullRequest':
        return github_state(info).pull_request(self, number)

    def pullRequests(self, info: GraphQLResolveInfo
                     ) -> 'PullRequestConnection':
        return PullRequestConnection(nodes=list(filter(
            lambda pr: self == pr.repository(info),
            github_state(info).pull_requests.values())))

    # TODO: This should take which repository the ref is in
    # This only works if you have upstream_sh
    def _make_ref(self, state: GitHubState, refName: str) -> 'Ref':
        # TODO: Probably should preserve object identity here when
        # you call this with refName/oid that are the same
        assert state.upstream_sh
        gitObject = GitObject(
            id=state.next_id(),
            # TODO: this upstream_sh hardcode wrong, but ok for now
            # because we only have one repo
            oid=GitObjectID(state.upstream_sh.git('rev-parse', refName)),
            _repository=self.id,
        )
        ref = Ref(
            id=state.next_id(),
            name=refName,
            _repository=self.id,
            target=gitObject,
        )
        return ref


@dataclass
class GitObject(Node):
    oid: GitObjectID
    _repository: GraphQLId

    def repository(self, info: GraphQLResolveInfo) -> Repository:
        return github_state(info).repositories[self._repository]


@dataclass
class Ref(Node):
    name: str
    _repository: GraphQLId
    target: GitObject

    def repository(self, info: GraphQLResolveInfo) -> Repository:
        return github_state(info).repositories[self._repository]


@dataclass
class PullRequest(Node):
    baseRef: Optional[Ref]
    baseRefName: str
    body: str
    # closed: bool
    headRef: Optional[Ref]
    headRefName: str
    # headRepository: Optional[Repository]
    # maintainerCanModify: bool
    number: GitHubNumber
    _repository: GraphQLId  # cycle breaker
    # state: PullRequestState
    title: str
    url: str

    def repository(self, info: GraphQLResolveInfo) -> Repository:
        return github_state(info).repositories[self._repository]


@dataclass
class PullRequestConnection:
    nodes: List[PullRequest]


class Root:
    def repository(self, info: GraphQLResolveInfo, owner: str,
                   name: str) -> Repository:
        return github_state(info).repository(owner, name)

    def node(self, info: GraphQLResolveInfo, id: GraphQLId) -> Node:
        if id in github_state(info).repositories:
            return github_state(info).repositories[id]
        elif id in github_state(info).pull_requests:
            return github_state(info).pull_requests[id]
        else:
            raise RuntimeError("unknown id {}".format(id))


with open(os.path.join(os.path.dirname(__file__),
          'github_schema.graphql')) as f:
    GITHUB_SCHEMA = graphql.build_schema(f.read())


# Ummm.  I thought there would be a way to stick these on the objects
# themselves (in the same way resolvers can be put on resolvers) but
# after a quick read of default_resolve_type_fn it doesn't look like
# we ever actually look to value for type of information.  This is
# pretty clunky lol.
def set_is_type_of(name: str, cls: Any) -> None:
    # Can't use a type ignore on the next line because fbcode
    # and us don't agree that it's necessary hmm.
    o: Any = GITHUB_SCHEMA.get_type(name)
    o.is_type_of = lambda obj, info: isinstance(obj, cls)


set_is_type_of('Repository', Repository)
set_is_type_of('PullRequest', PullRequest)


class FakeGitHubEndpoint(ghstack.github.GitHubEndpoint):
    state: GitHubState

    def __init__(self,
                 upstream_sh: Optional[ghstack.shell.Shell] = None
                 ) -> None:
        self.state = GitHubState(upstream_sh)

    def graphql(self, query: str, **kwargs: Any) -> Any:
        r = graphql.graphql_sync(
            schema=GITHUB_SCHEMA,
            source=query,
            root_value=self.state.root,
            context_value=self.state,
            variable_values=kwargs)
        if r.errors:
            # The GraphQL implementation loses all the stack traces!!!
            # D:  You can 'recover' them by deleting the
            # 'except Exception as error' from GraphQL-core-next; need
            # to file a bug report
            raise RuntimeError("GraphQL query failed with errors:\n\n{}"
                               .format("\n".join(str(e) for e in r.errors)))
        # The top-level object isn't indexable by strings, but
        # everything underneath is, oddly enough
        return {'data': r.data}

    def push_hook(self, refNames: Sequence[str]) -> None:
        self.state.push_hook(refNames)

    # NB: This technically does have a payload, but we don't
    # use it so I didn't bother constructing it.
    def _create_pull(self, owner: str, name: str,
                     input: CreatePullRequestInput) -> CreatePullRequestPayload:
        state = self.state
        id = state.next_id()
        repo = state.repository(owner, name)
        number = state.next_pull_request_number(repo.id)
        baseRef = None
        headRef = None
        # TODO: When we support forks, this needs rewriting to stop
        # hard coded the repo we opened the pull request on
        if state.upstream_sh:
            baseRef = repo._make_ref(state, input['base'])
            headRef = repo._make_ref(state, input['head'])
        pr = PullRequest(
            id=id,
            _repository=repo.id,
            number=number,
            url="https://github.com/{}/pull/{}"
                .format(repo.nameWithOwner, number),
            baseRef=baseRef,
            baseRefName=input['base'],
            headRef=headRef,
            headRefName=input['head'],
            title=input['title'],
            body=input['body'],
        )
        # TODO: compute files changed
        state.pull_requests[id] = pr
        # This is only a subset of what the actual REST endpoint
        # returns.
        return {
            'number': number,
        }

    # NB: This technically does have a payload, but we don't
    # use it so I didn't bother constructing it.
    def _update_pull(self, owner: str, name: str, number: GitHubNumber,
                     input: UpdatePullRequestInput) -> None:
        state = self.state
        repo = state.repository(owner, name)
        pr = state.pull_request(repo, number)
        # If I say input.get('title') is not None, mypy
        # is unable to infer input['title'] is not None
        if 'title' in input and input['title'] is not None:
            pr.title = input['title']
        if 'base' in input and input['base'] is not None:
            pr.baseRefName = input['base']
            pr.baseRef = repo._make_ref(state, pr.baseRefName)
        if 'body' in input and input['body'] is not None:
            pr.body = input['body']

    def rest(self, method: str, path: str, **kwargs: Any) -> Any:
        if method == 'post':
            m = re.match(r'^repos/([^/]+)/([^/]+)/pulls$', path)
            if m:
                return self._create_pull(m.group(1), m.group(2),
                                         cast(CreatePullRequestInput, kwargs))
        elif method == 'patch':
            m = re.match(r'^repos/([^/]+)/([^/]+)/pulls/([^/]+)$', path)
            if m:
                return self._update_pull(
                    m.group(1), m.group(2), GitHubNumber(int(m.group(3))),
                    cast(UpdatePullRequestInput, kwargs))
        raise NotImplementedError(
            "FakeGitHubEndpoint REST {} {} not implemented"
            .format(method.upper(), path)
        )
