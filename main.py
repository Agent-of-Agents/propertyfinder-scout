"""CLI-раннер агента «propertyfinder-scout».

    python main.py "твой запрос"
"""

from __future__ import annotations

import sys

from agent import run
from config import AgentConfig


def main(argv: list[str]) -> int:
    if not argv:
        print('Использование: python main.py "твой запрос"', file=sys.stderr)
        return 2

    config = AgentConfig.from_env()
    config.require_api_key()
    print(run(" ".join(argv), config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
