"""Render a basket JSON file (one frontier option) to a PDF.

Usage inside the Docker container:
    cp-mcp-render <input.basket.json> <output.pdf>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cp_mcp.basket import BasketSolution
from cp_mcp.format import format_pdf


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    payload = json.loads(in_path.read_text())
    if "solution" in payload and isinstance(payload["solution"], dict):
        sol = BasketSolution.model_validate(payload["solution"])
    else:
        sol = BasketSolution.model_validate(payload)
    out = format_pdf(sol, out_path)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
