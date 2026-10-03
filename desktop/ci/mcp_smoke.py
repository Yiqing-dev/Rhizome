# SPDX-License-Identifier: Apache-2.0
"""CI: start the frozen `rhz.exe mcp` over stdio, list tools and run one search."""

import asyncio
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(exe: str) -> None:
    params = StdioServerParameters(command=exe, args=["mcp"], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            t0 = time.time()
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"rhz_ingest", "rhz_recall", "rhz_search"} <= names, names
            print(f"initialized + listed tools in {time.time() - t0:.1f}s", flush=True)
            res = await s.call_tool("rhz_search", {"query": "RootNet", "types": "method"})
            print(f"first call in {time.time() - t0:.1f}s", flush=True)
            assert "RootNet" in res.content[0].text, res.content[0].text[:200]
            print("MCP OK:", sorted(names))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(sys.argv[1]), timeout=120))
