"""CLI entrypoint: stdio or HTTP SSE MCP transport."""

from __future__ import annotations

import argparse

from teamrag.mcp_server.server import run_sse, run_stdio


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="teamrag-mcp",
        description="TeamRag MCP server (stdio for local IDEs, sse for HTTP).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse"),
        default="stdio",
        help="Transport: stdio (default) or sse (HTTP server)",
    )
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    else:
        run_sse()


if __name__ == "__main__":
    main()
