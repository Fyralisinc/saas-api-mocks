"""``python -m spammers.carta run`` — launch the Carta mock."""
from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser(prog="spammers.carta")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the Carta mock server")
    p_run.add_argument("--host", default="0.0.0.0")
    p_run.add_argument("--port", type=int, default=7020)
    p_run.add_argument("--reload", action="store_true")

    args = parser.parse_args()
    if args.cmd == "run":
        uvicorn.run("spammers.carta.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
