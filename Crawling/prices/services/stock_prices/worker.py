"""One bounded provider call, invoked by the collector in an isolated process."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
import io
import json
import sys


def main():
    try:
        if len(sys.argv) != 5:
            raise ValueError("invalid_arguments")
        # Some libraries print import-time login notices; they are not our protocol.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from .providers import fetch_prices
            rows = fetch_prices(sys.argv[1], sys.argv[2], date.fromisoformat(sys.argv[3]), date.fromisoformat(sys.argv[4]))
        result = {"ok": True, "rows": rows}
    except Exception as error:
        result = {"ok": False, "error": getattr(error, "kind", type(error).__name__),
                  "halt_source": bool(getattr(error, "halt_source", False))}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
