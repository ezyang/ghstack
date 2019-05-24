#!/usr/bin/env python3

from typing import Optional, Any
import logging
import json
import aiohttp
import re

import ghstack.circleci
import ghstack.cache


RE_BUILD_PATH = re.compile(r'^project/github/[^/]+/[^/]+/[0-9]+$')


class RealCircleCIEndpoint(ghstack.circleci.CircleCIEndpoint):
    rest_endpoint: str = 'https://circleci.com/api/v1.1'

    # The API token to authenticate to CircleCI with
    # https://circleci.com/account/api
    circle_token: Optional[str]

    # The URL of a proxy to use for these connections (for
    # Facebook users, this is typically 'http://fwdproxy:8080')
    proxy: Optional[str]

    def __init__(self,
                 *,
                 circle_token: Optional[str] = None,
                 proxy: Optional[str] = None):
        self.circle_token = circle_token
        self.proxy = proxy

    async def rest(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'ghstack',
        }

        url = self.rest_endpoint + '/' + path
        logging.debug("# {} {}".format(method, url))
        logging.debug("Request body:\n{}".format(json.dumps(kwargs, indent=1)))

        params = {}
        if self.circle_token:
            params['circle-token'] = self.circle_token

        is_get_build = method == 'get' and RE_BUILD_PATH.match(path)

        if is_get_build:
            # consult cache
            cache_result = ghstack.cache.get('circleci', path)
            if cache_result is not None:
                logging.debug("Retrieved result from cache")
                return json.loads(cache_result)

        async with aiohttp.request(
                method.upper(),
                url,
                params=params,
                json=kwargs,
                headers=headers,
                proxy=self.proxy,
        ) as resp:
            logging.debug("Response status: {}".format(resp.status))

            r_text = await resp.text()

            try:
                r = json.loads(r_text)
            except json.decoder.JSONDecodeError:
                logging.debug("Response body:\n{}".format(r_text))
                raise
            else:
                pretty_json = json.dumps(r, indent=1)
                logging.debug("Response JSON:\n{}".format(pretty_json))

            try:
                resp.raise_for_status()
            except aiohttp.ClientResponseError:
                raise RuntimeError(pretty_json)

            # NB: Don't save to cache if it's still running
            if is_get_build and r["outcome"] is not None:
                logging.debug("Saving result to cache")
                ghstack.cache.put('circleci', path, r_text)

            return r
