#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# This script looks at all commits downloads their logs and prints them
# for you

import ghstack.github
import ghstack.circleci
import ghstack.github_utils

import re
from typing import Any, Dict
import aiohttp

RE_CIRCLECI_URL = re.compile(r'^https://circleci.com/gh/pytorch/pytorch/([0-9]+)')


def strip_sccache(x: str) -> str:
    sccache_marker = "=================== sccache compilation log ==================="
    marker_pos = x.rfind(sccache_marker)
    newline_before_marker_pos = x.rfind('\n', 0, marker_pos)
    return x[:newline_before_marker_pos]


async def main(pull_request: str,
         github: ghstack.github.GitHubEndpoint,
         circleci: ghstack.circleci.CircleCIEndpoint) -> None:

    # Game plan:
    # 1. Query GitHub to find out what the current statuses are
    #       (TODO: if we got rate limited we'll miss stuff)
    # 2. For each status in parallel:
    #   a. Query CircleCI for job status
    #   b. (Future work) Query output_url to get log information
    #      (it's gzip'ed)
    #
    # For now:
    #   - Print if the job actually ran, or was skipped
    #       - Easy way to determine: check if "Should run job after
    #         checkout" is last step
    #       - I inspected circleci.get('project/github/pytorch/pytorch/1773555')
    #         to see if there were other options, there did not appear
    #         to be any indication that a halt was called.  So we'll
    #         have to rely on the (OS X jobs, take note!)

    params = ghstack.github_utils.parse_pull_request(pull_request)

    # TODO: stop hard-coding number of commits
    r = github.graphql("""
    query ($name: String!, $owner: String!, $number: Int!) {
        repository(name: $name, owner: $owner) {
            pullRequest(number: $number) {
                commits(last: 100) {
                    nodes {
                        commit {
                            oid
                            messageHeadline
                            status {
                                contexts {
                                    context
                                    state
                                    targetUrl
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    """, **params)
    nodes = r['data']['repository']['pullRequest']['commits']['nodes']

    async def process_node(n: Dict[str, Any]) -> str:
        commit = n['commit']
        status = commit['status']
        icon = "❔"
        text = ""
        buildid_text = ""
        if status is not None:
            contexts = status['contexts']
        else:
            contexts = []
        for c in contexts:
            # TODO: Stop hard-coding me
            if c['context'] != 'ci/circleci: pytorch_linux_xenial_py3_clang5_asan_test':
                continue
            m = RE_CIRCLECI_URL.match(c['targetUrl'])
            if not m:
                icon = "🍆"
                break
            if c['state'] == 'SUCCESS':
                icon = "✅"
                break
            buildid = m.group(1)
            buildid_text = " ({})".format(buildid)
            r = await circleci.get("project/github/{name}/{owner}/{buildid}".format(buildid=buildid, **params))
            if not r["failed"]:
                # It was just cancelled (don't check "cancelled"; that's
                # true even if the job failed otherwise; it just means
                # workflow got cancelled)
                icon = "❔"
                break
            icon = "❌"
            async with aiohttp.request('get', r['steps'][-1]['actions'][-1]['output_url']) as resp:
                log_json = await resp.json()
                buf = []
                for e in log_json:
                    buf.append(e["message"])
                text = "\n" + strip_sccache("\n".join(buf))
                text = text[-1500:]
        return "{} {} {}{}{}".format(icon, commit['oid'][:8], commit['messageHeadline'], buildid_text, text)

    for n in nodes:
        print(await process_node(n))
