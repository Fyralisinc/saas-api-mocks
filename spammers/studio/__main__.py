"""``python -m spammers.studio`` — launch the Studio control UI."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Best-effort: load a local .env so SPAMMERS_DB_URL is set when run
    directly (dev.sh already does this; this covers the bare invocation)."""
    for path in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break


def main() -> None:
    p = argparse.ArgumentParser(prog="spammers.studio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7000)
    args = p.parse_args()

    _load_dotenv()
    import uvicorn
    uvicorn.run("spammers.studio.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
