# ghstack development

## Lint

Run `python -m black src/ghstack/` and `python -m flake8 src/ghstack/` to fix and check lint before committing. If lint fixes are needed after a commit, amend the commit rather than creating a separate lint fix commit.

## ghstack trailers

When rewriting, splitting, rebasing, or autosquashing a ghstack stack, preserve existing `ghstack-source-id`, `ghstack-comment-id`, and `Pull-Request` trailers on each logical commit. Before submitting a rewritten stack, compare commit messages against the saved/pre-rewrite commits or search local history for an existing `Pull-Request` trailer with the same subject. If a logical commit was already submitted, keep its existing `Pull-Request` and `ghstack-comment-id` instead of letting ghstack create a new PR.
