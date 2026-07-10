"""Enable ``python -m cendor_init`` in addition to the ``cendor-init`` console script / ``uvx``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
