# ghstack Merge Workflow Examples

These examples show how to use the reusable `ghstack-merge.yml` workflow in your repository.

## Quick Start

1. Copy one of the example workflows to your repository's `.github/workflows/` directory
2. Optionally create `.github/merge_rules.yaml` to define merge rules
3. Comment `@ghstack merge` on any ghstack PR to trigger landing

## Examples

| File | Description |
|------|-------------|
| `ghstack-merge-basic.yml` | Minimal setup - just lands PRs on `@ghstack merge` |
| `ghstack-merge-with-rules.yml` | Validates against merge rules before landing |
| `ghstack-merge-dry-run.yml` | Validates without merging (triggered by `@ghstack check`) |
| `ghstack-merge-custom.yml` | Shows all available configuration options |

## Workflow Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `pull_request_number` | number | Yes | - | PR number to land |
| `python_version` | string | No | `'3.11'` | Python version |
| `ghstack_version` | string | No | `'ghstack'` | ghstack package spec |
| `validate_rules` | boolean | No | `true` | Validate against merge rules |
| `dry_run` | boolean | No | `false` | Validate only, don't merge |
| `comment_on_failure` | boolean | No | `true` | Post errors as PR comment |
| `runs_on` | string | No | `'ubuntu-latest'` | GitHub runner to use |

## Secrets

| Secret | Required | Description |
|--------|----------|-------------|
| `github_token` | Yes | Token with `contents:write` and `pull-requests:write` |

## Merge Rules Configuration

Create `.github/merge_rules.yaml` in your repository:

```yaml
# Rules are matched in order - first matching rule wins
- name: core-library
  patterns:
    - "src/**"
  approved_by:
    - maintainer-username
    - org/team-slug          # Team reference
  mandatory_checks_name:
    - "Test"
    - "Lint"

- name: documentation
  patterns:
    - "docs/**"
    - "*.md"
  approved_by:
    - any                    # Any approval is sufficient
  mandatory_checks_name:
    - "Lint"

- name: default
  patterns:
    - "**/*"
  approved_by:
    - maintainer-username
  mandatory_checks_name:
    - "Test"
```

### Rule Fields

| Field | Description |
|-------|-------------|
| `name` | Rule identifier (shown in error messages) |
| `patterns` | File glob patterns (fnmatch syntax) |
| `approved_by` | Required approvers (usernames, `org/team`, or `any`) |
| `mandatory_checks_name` | CI checks that must pass |
| `ignore_flaky_failures` | If `true`, ignore check failures (optional) |

## Error Comments

When validation fails with `comment_on_failure: true`, the workflow posts a comment like:

```markdown
## Merge validation failed for PR #123

**Rule:** core-library

### Errors:
- Missing required approval from: maintainer-username, org/team-slug
- Check "Test" has not passed (status: failure)

### Matched Files:
- `src/main.py`
- `src/utils.py`
```
