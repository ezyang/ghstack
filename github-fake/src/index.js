const fs = require('fs');
const { ApolloServer, makeExecutableSchema } = require('apollo-server');
const { mergeSchemas } = require('graphql-tools');

function requireGraphQL(name) {
  const filename = require.resolve(name);
  return fs.readFileSync(filename, 'utf8');
}

let REPOSITORIES = {};
let PULL_REQUESTS = {};
let NEXT_ID = 5000;

function reset() {
  REPOSITORIES = {
    1000: {
      id: 1000,
      name: "pytorch",
      nameWithOwner: "pytorch/pytorch",
      nextPullRequestNumber: 500,
    }
  };
  PULL_REQUESTS = {};
  NEXT_ID = 5000;
  /*
  PULL_REQUESTS = {
    2000: {
      id: 1001,
      repository: 1000,
      number: 100,
      url: "https://github.com/pytorch/pytorch/pull/22",
      baseRefName: "master",
      headRefName: "pr/my-little-pr",
      title: "My little pull request",
      body: "A nice interesting pull request to test with",
    }
  };
  */
}

reset();

// Type definitions define the "shape" of your data and specify
// which ways the data can be fetched from the GraphQL server.
const githubTypeDefs = requireGraphQL('./schema.graphql');

const controlResolvers = {
  Mutation: {
    resetGitHub: (root, args) => {
      reset();
    }
  }
};

const githubResolvers = {
  Query: {
    repository: (root, args) =>
      Object.values(REPOSITORIES).find((repo) => repo.nameWithOwner == (args.owner + "/" + args.name))
    ,
    node: (root, args) => {
      if (args.id in REPOSITORIES) {
        return REPOSITORIES[args.id];
      } else if (args.id in PULL_REQUESTS) {
        return PULL_REQUESTS[args.id];
      }
    }
  },
  Node: {
    __resolveType(obj, context, info) {
      if (obj.nameWithOwner) {
        return 'Repository';
      } else if (obj.headRefName) {
        return 'PullRequest';
      }
      return null;
    }
  },
  Repository: {
    pullRequest: (root, args) =>
      Object.values(PULL_REQUESTS).find((pr) => root.id == pr.repository && pr.number == args.number)
    ,
    pullRequests: (root, args) => {
      if (Object.keys(args).length) {
        throw new Error("pullRequest inputs not supported: " + Object.keys(args));
      }
      // Pagination? What's that?
      return { nodes: Object.values(PULL_REQUESTS).filter((pr) => root.id == pr.repository) }
    },
  },
  Mutation: {
    updatePullRequest: (root, args) => {
      const pullRequest = PULL_REQUESTS[args.input.pullRequestId];
      if (args.input.title !== undefined) pullRequest.title = args.input.title;
      if (args.input.baseRefName !== undefined) pullRequest.baseRefName = args.input.baseRefName;
      if (args.input.body !== undefined) pullRequest.body = args.input.body;
      return { pullRequest };
    },
    createPullRequest: (root, args) => {
      const id = NEXT_ID++;
      const repo = REPOSITORIES[args.input.ownerId];
      const number = repo.nextPullRequestNumber++;
      const pullRequest = {
        id,
        repository: args.input.ownerId,
        number: number,
        url: "https://github.com/" + repo.nameWithOwner + "/pull/" + number,
        baseRefName: args.input.baseRefName,
        headRefName: args.input.headRefName,
        title: args.input.title,
        body: args.input.body,
      }
      PULL_REQUESTS[id] = pullRequest;
      return { pullRequest };
    }
  },
};

const resolverValidationOptions = { requireResolversForResolveType: false };

const controlSchema = makeExecutableSchema({
  typeDefs: `
    type Query {
      dummy: String
    }
    input ResetGitHubInput {
      clientMutationId: String
    }
    type ResetGitHubPayload {
      clientMutationId: String
    }
    type Mutation {
      resetGitHub(input: ResetGitHubInput!): ResetGitHubPayload
    }
  `,
  resolvers: controlResolvers,
});
const githubSchema = makeExecutableSchema({ typeDefs: githubTypeDefs, resolvers: githubResolvers, resolverValidationOptions });

const schema = mergeSchemas({
  schemas: [
    controlSchema,
    githubSchema,
  ],
});

const server = new ApolloServer({ schema });

// This `listen` method launches a web-server.  Existing apps
// can utilize middleware options, which we'll discuss later.
const port = process.argv[2] ? process.argv[2] : 4000;
server.listen(port).then(({ url }) => {
  console.log(`${url}`);
});
