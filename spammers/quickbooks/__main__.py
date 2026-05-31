"""``python -m spammers.quickbooks run`` — launch the QuickBooks Online mock."""
from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser(prog="spammers.quickbooks")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the QuickBooks Online mock server")
    p_run.add_argument("--host", default="0.0.0.0")
    p_run.add_argument("--port", type=int, default=7009)
    p_run.add_argument("--reload", action="store_true")

    args = parser.parse_args()
    if args.cmd == "run":
        uvicorn.run("spammers.quickbooks.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
