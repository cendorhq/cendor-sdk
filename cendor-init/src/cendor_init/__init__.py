"""cendor-init — offline CLI to wire Cendor + your AI assistant (``init``) and validate the wiring
(``doctor``). Optional developer tooling; no Cendor library depends on it, and it makes no network
call. See https://cendor.ai/docs/for-ai-assistants.

This is a standalone top-level package (``cendor_init``) — NOT part of the PEP 420 ``cendor.*``
namespace — mirroring how ``cendor-mcp`` ships ``cendor_mcp``.
"""

from __future__ import annotations

from .doctor import run_doctor
from .initialize import run_init

__all__ = ["run_init", "run_doctor", "__version__"]
__version__ = "0.1.1"
