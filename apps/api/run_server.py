"""Local dev server launcher (Windows-safe).

psycopg3 async requires a Selector event loop on Windows, so this entry
installs the policy before uvicorn builds its loop. Use instead of bare
`uvicorn` when running the API on a Windows host:

    python run_server.py [--port 8000]

(In Docker the container is Linux and `uvicorn app.main:app` is fine.)
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    import uvicorn

    if sys.platform == "win32":
        import selectors

        config = uvicorn.Config(
            "app.main:app", host=args.host, port=args.port, log_level="info", loop="asyncio"
        )
        # psycopg3 async cannot run on Windows' default Proactor loop
        config.get_loop_factory = lambda: (
            lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
        uvicorn.Server(config).run()
    else:
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
