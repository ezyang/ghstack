#!/usr/bin/env python3
"""
Benchmark ghstack submit against a real GitHub repository.

Usage:
    python bench/bench_submit.py --repo owner/repo [--token TOKEN] [--iterations N] [--stack-size N]

The repo should be a throwaway playground repo you don't mind having
test PRs created in.  PRs are closed (not deleted) after each run.

Requires GITHUB_TOKEN env var or --token flag.
"""

import argparse
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple


def run(args: List[str], cwd: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kwargs)


def git(cwd: str, *args: str, check: bool = True) -> str:
    r = run(["git", *args], cwd=cwd)
    if check and r.returncode != 0:
        print(f"git {' '.join(args)} failed:\n{r.stderr}", file=sys.stderr)
        raise RuntimeError(f"git failed: {r.stderr}")
    return r.stdout.strip()


def parse_timing(stderr: str) -> Dict[str, float]:
    timing: Dict[str, float] = {}
    for line in stderr.splitlines():
        m = re.match(r"\[ghstack timing\] (.+): (\d+)ms", line)
        if m:
            timing[m.group(1)] = float(m.group(2))
    return timing


def close_prs(repo: str, token: str, pr_numbers: List[int]) -> None:
    """Close PRs via GitHub API."""
    import requests

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    for num in pr_numbers:
        requests.patch(
            f"https://api.github.com/repos/{repo}/pulls/{num}",
            headers=headers,
            json={"state": "closed"},
        )


def extract_pr_numbers(stdout: str) -> List[int]:
    return [int(m) for m in re.findall(r"/pull/(\d+)", stdout)]


def run_benchmark(
    repo: str,
    token: str,
    stack_size: int,
    username: str,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Run one benchmark iteration. Returns (create_timing, update_timing)."""
    workdir = tempfile.mkdtemp(prefix="ghstack-bench-")
    try:
        # Clone
        git(
            workdir,
            "clone",
            f"https://x-access-token:{token}@github.com/{repo}.git",
            "repo",
        )
        repo_dir = os.path.join(workdir, "repo")
        git(repo_dir, "config", "user.name", "ghstack-bench")
        git(repo_dir, "config", "user.email", "bench@ghstack.dev")

        # Create N commits
        for i in range(stack_size):
            fname = os.path.join(repo_dir, f"bench_{i}.txt")
            with open(fname, "w") as f:
                f.write(f"commit {i}\n")
            git(repo_dir, "add", fname)
            git(
                repo_dir,
                "commit",
                "-m",
                f"Bench commit {i}\n\nThis is bench commit {i}",
            )

        # First submit (creates PRs)
        env = {
            **os.environ,
            "GHSTACK_TIMING": "1",
            "GITHUB_TOKEN": token,
        }
        r = run(
            [sys.executable, "-m", "ghstack", "submit", "-m", "Bench create"],
            cwd=repo_dir,
            env=env,
        )
        if r.returncode != 0:
            print(
                f"ghstack submit (create) failed:\n{r.stdout}\n{r.stderr}",
                file=sys.stderr,
            )
            raise RuntimeError("ghstack submit failed")
        create_timing = parse_timing(r.stderr)
        pr_numbers = extract_pr_numbers(r.stdout)

        # Amend all commits (update PRs)
        for i in range(stack_size):
            fname = os.path.join(repo_dir, f"bench_{i}.txt")
            with open(fname, "w") as f:
                f.write(f"commit {i} updated\n")
            git(repo_dir, "add", fname)

        # Amend top commit
        git(repo_dir, "commit", "--amend", "--no-edit")

        r = run(
            [sys.executable, "-m", "ghstack", "submit", "-m", "Bench update"],
            cwd=repo_dir,
            env=env,
        )
        if r.returncode != 0:
            print(
                f"ghstack submit (update) failed:\n{r.stdout}\n{r.stderr}",
                file=sys.stderr,
            )
            raise RuntimeError("ghstack submit failed")
        update_timing = parse_timing(r.stderr)

        # Close PRs
        if pr_numbers:
            close_prs(repo, token, pr_numbers)

        return create_timing, update_timing
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark ghstack submit")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    parser.add_argument(
        "--token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token"
    )
    parser.add_argument(
        "--iterations", type=int, default=3, help="Number of iterations"
    )
    parser.add_argument(
        "--stack-size", type=int, default=3, help="Number of commits in stack"
    )
    parser.add_argument(
        "--username", default=None, help="GitHub username (auto-detected if not set)"
    )
    args = parser.parse_args()

    if not args.token:
        print("Error: GITHUB_TOKEN env var or --token required", file=sys.stderr)
        sys.exit(1)

    if args.username is None:
        import requests

        r = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {args.token}"},
        )
        args.username = r.json()["login"]

    print(f"Benchmarking ghstack submit against {args.repo}")
    print(f"  Stack size: {args.stack_size}")
    print(f"  Iterations: {args.iterations}")
    print(f"  Username: {args.username}")
    print()

    create_timings: List[Dict[str, float]] = []
    update_timings: List[Dict[str, float]] = []

    for i in range(args.iterations):
        print(f"Iteration {i + 1}/{args.iterations}...", end=" ", flush=True)
        create, update = run_benchmark(
            args.repo, args.token, args.stack_size, args.username
        )
        create_timings.append(create)
        update_timings.append(update)
        print(
            f"create={create.get('total', 0):.0f}ms  update={update.get('total', 0):.0f}ms"
        )

    print()
    print("=== Results (median over {} iterations) ===".format(args.iterations))
    print()

    for label, timings in [("CREATE", create_timings), ("UPDATE", update_timings)]:
        print(f"  {label}:")
        all_keys = sorted(set(k for t in timings for k in t.keys()))
        for key in all_keys:
            values = [t.get(key, 0) for t in timings]
            median = statistics.median(values)
            print(f"    {key:25s} {median:7.0f}ms")
        print()


if __name__ == "__main__":
    main()
