"""``python -m spammers.deel run`` — launch the Deel mock."""
from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser(prog="spammers.deel")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the Deel mock server")
    p_run.add_argument("--host", default="0.0.0.0")
    p_run.add_argument("--port", type=int, default=7014)
    p_run.add_argument("--reload", action="store_true")

    args = parser.parse_args()
    if args.cmd == "run":
        uvicorn.run("spammers.deel.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
