#!/usr/bin/env python3

"""
Merge Rules Engine for ghstack land.

This module provides functionality to load, parse, and validate merge rules
that control when PRs can be landed. Rules specify required approvers and
CI checks based on file patterns.
"""

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import yaml

import ghstack.github


class MergeValidationError(RuntimeError):
    """Raised when merge validation fails."""

    def __init__(self, result: "ValidationResult"):
        self.result = result
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        lines = [f"Merge validation failed for PR #{self.result.pr_number}"]
        if self.result.rule_name:
            lines.append(f"Rule: {self.result.rule_name}")
        if self.result.errors:
            lines.append("Errors:")
            for error in self.result.errors:
                lines.append(f"  - {error}")
        return "\n".join(lines)


@dataclass
class MergeRule:
    """Represents a single merge rule configuration."""

    name: str
    patterns: List[str]
    approved_by: List[str]
    mandatory_checks_name: List[str]
    ignore_flaky_failures: bool = False


@dataclass
class ValidationResult:
    """Result of validating a PR against merge rules."""

    valid: bool
    pr_number: int
    rule_name: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    matched_files: List[str] = field(default_factory=list)


class MergeRulesLoader:
    """Loads merge rules from repository or local files."""

    def __init__(
        self,
        github: "ghstack.github.GitHubEndpoint",
        owner: str,
        repo: str,
    ):
        self.github = github
        self.owner = owner
        self.repo = repo

    def load_from_repo(self, ref: str = "HEAD") -> List[MergeRule]:
        """Load merge rules from the repository's .github/merge_rules.yaml file."""
        try:
            content = self.github.get_file_contents(
                self.owner, self.repo, ".github/merge_rules.yaml", ref
            )
            return self._parse_yaml(content)
        except ghstack.github.NotFoundError:
            logging.debug("No merge_rules.yaml found in repository")
            return []
        except Exception as e:
            logging.warning(f"Failed to load merge rules: {e}")
            return []

    def load_from_file(self, path: str) -> List[MergeRule]:
        """Load merge rules from a local file path."""
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return self._parse_yaml(content)

    def _parse_yaml(self, content: str) -> List[MergeRule]:
        """Parse YAML content into a list of MergeRule objects."""
        data = yaml.safe_load(content)
        if not isinstance(data, list):
            raise ValueError("merge_rules.yaml must be a list of rules")

        rules = []
        for item in data:
            rule = MergeRule(
                name=item.get("name", "unnamed"),
                patterns=item.get("patterns", []),
                approved_by=item.get("approved_by", []),
                mandatory_checks_name=item.get("mandatory_checks_name", []),
                ignore_flaky_failures=item.get("ignore_flaky_failures", False),
            )
            rules.append(rule)
        return rules


class MergeValidator:
    """Validates PRs against merge rules."""

    def __init__(
        self,
        github: "ghstack.github.GitHubEndpoint",
        owner: str,
        repo: str,
    ):
        self.github = github
        self.owner = owner
        self.repo = repo
        self._team_cache: Dict[str, Set[str]] = {}

    def get_pr_files(self, pr_number: int) -> List[str]:
        """Get list of files changed in a PR."""
        files = self.github.get_pr_files(self.owner, self.repo, pr_number)
        return [f["filename"] for f in files]

    def get_pr_approvers(self, pr_number: int) -> Set[str]:
        """Get set of users who have approved the PR."""
        reviews = self.github.get_pr_reviews(self.owner, self.repo, pr_number)
        approvers: Set[str] = set()

        # Track the latest review state for each user
        user_states: Dict[str, str] = {}
        for review in reviews:
            user = review.get("user", {}).get("login", "")
            state = review.get("state", "")
            if user and state:
                user_states[user] = state

        # Only count users whose latest review is APPROVED
        for user, state in user_states.items():
            if state == "APPROVED":
                approvers.add(user)

        return approvers

    def get_pr_check_statuses(self, pr_number: int) -> Dict[str, str]:
        """Get CI check statuses for a PR's head commit."""
        # First get the PR to find the head SHA
        pr_info = self.github.get(
            f"repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        )
        head_sha = pr_info.get("head", {}).get("sha", "")
        if not head_sha:
            return {}

        check_runs = self.github.get_check_runs(self.owner, self.repo, head_sha)
        statuses: Dict[str, str] = {}
        for check in check_runs.get("check_runs", []):
            name = check.get("name", "")
            conclusion = check.get("conclusion")
            status = check.get("status", "")
            # Use conclusion if available, otherwise use status
            if conclusion:
                statuses[name] = conclusion
            else:
                statuses[name] = status
        return statuses

    def expand_team_members(self, team_ref: str) -> Set[str]:
        """
        Expand a team reference (org/team-slug) to its members.

        Returns an empty set if the reference isn't a team or if the
        API call fails.
        """
        if "/" not in team_ref:
            # Not a team reference, return as single user
            return {team_ref}

        if team_ref in self._team_cache:
            return self._team_cache[team_ref]

        try:
            org, team_slug = team_ref.split("/", 1)
            members = self.github.get_team_members(org, team_slug)
            member_logins = {m.get("login", "") for m in members if m.get("login")}
            self._team_cache[team_ref] = member_logins
            return member_logins
        except Exception as e:
            logging.warning(f"Failed to expand team {team_ref}: {e}")
            # Return as single user if team expansion fails
            return {team_ref}

    def find_matching_rule(
        self, files: List[str], rules: List[MergeRule]
    ) -> Optional[MergeRule]:
        """
        Find the first rule that matches any of the given files.

        Rules are matched in order - first matching rule wins.
        """
        for rule in rules:
            for file_path in files:
                for pattern in rule.patterns:
                    if fnmatch.fnmatch(file_path, pattern):
                        return rule
        return None

    def validate_pr(
        self, pr_number: int, rules: List[MergeRule]
    ) -> ValidationResult:
        """
        Validate a PR against the provided merge rules.

        Returns a ValidationResult indicating whether the PR passes
        all required checks and approvals.
        """
        files = self.get_pr_files(pr_number)

        if not files:
            return ValidationResult(
                valid=True,
                pr_number=pr_number,
                rule_name=None,
                errors=[],
                matched_files=[],
            )

        rule = self.find_matching_rule(files, rules)
        if rule is None:
            # No matching rule, PR passes by default
            return ValidationResult(
                valid=True,
                pr_number=pr_number,
                rule_name=None,
                errors=[],
                matched_files=files,
            )

        errors: List[str] = []

        # Validate approvers
        if rule.approved_by:
            if "any" not in rule.approved_by:
                approvers = self.get_pr_approvers(pr_number)
                required_approvers: Set[str] = set()

                for approver_ref in rule.approved_by:
                    required_approvers.update(self.expand_team_members(approver_ref))

                if not approvers.intersection(required_approvers):
                    missing = ", ".join(sorted(rule.approved_by))
                    errors.append(f"Missing required approval from: {missing}")

        # Validate CI checks
        if rule.mandatory_checks_name:
            check_statuses = self.get_pr_check_statuses(pr_number)

            for check_name in rule.mandatory_checks_name:
                status = check_statuses.get(check_name, "missing")
                if status == "missing":
                    errors.append(f'Check "{check_name}" has not run')
                elif status not in ("success", "neutral", "skipped"):
                    if status == "in_progress" or status == "queued":
                        errors.append(
                            f'Check "{check_name}" has not completed (status: {status})'
                        )
                    elif rule.ignore_flaky_failures:
                        logging.info(
                            f'Check "{check_name}" failed but ignore_flaky_failures is set'
                        )
                    else:
                        errors.append(
                            f'Check "{check_name}" has not passed (status: {status})'
                        )

        return ValidationResult(
            valid=len(errors) == 0,
            pr_number=pr_number,
            rule_name=rule.name,
            errors=errors,
            matched_files=files,
        )


def format_validation_error_comment(result: ValidationResult) -> str:
    """Format a validation result as a markdown comment for posting to GitHub."""
    lines = [f"## Merge validation failed for PR #{result.pr_number}"]

    if result.rule_name:
        lines.append(f"\n**Rule:** {result.rule_name}")

    if result.errors:
        lines.append("\n### Errors:")
        for error in result.errors:
            lines.append(f"- {error}")

    if result.matched_files:
        lines.append("\n### Matched Files:")
        for file_path in result.matched_files[:10]:  # Limit to first 10
            lines.append(f"- `{file_path}`")
        if len(result.matched_files) > 10:
            lines.append(f"- ... and {len(result.matched_files) - 10} more files")

    return "\n".join(lines)
