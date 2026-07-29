# ghstack development

## Lint

Run `uv run --frozen lintrunner --all-files` to reproduce CI lint locally before committing. Invoke lintrunner through `uv run` unless the project virtual environment is active; otherwise its Python-based linters may use a system Python without the development dependencies. To apply formatter fixes, run `uv run --frozen lintrunner format --all-files`, then rerun the full lint command. If lint fixes are needed after a commit, amend the commit rather than creating a separate lint fix commit.

## Tests

The full test suite takes quite a long time to run. Prefer focused tests while iterating, and only run the full suite when the change is otherwise done.

## ghstack trailers

When rewriting, splitting, rebasing, or autosquashing a ghstack stack, preserve existing `ghstack-source-id`, `ghstack-comment-id`, and `Pull-Request` trailers on each logical commit. Before submitting a rewritten stack, compare commit messages against the saved/pre-rewrite commits or search local history for an existing `Pull-Request` trailer with the same subject. If a logical commit was already submitted, keep its existing `Pull-Request` and `ghstack-comment-id` instead of letting ghstack create a new PR.
