"""Policy deployment artifact naming and hashing helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

__all__ = ["_content_hash", "_external_name"]

def _content_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



def _external_name(prefix: str, policy_name: str, hostname: str, value: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{policy_name}-{hostname}".lower()).strip("-")
    return f"klos-{prefix}-{slug[:180]}-{_content_hash(value)[:12]}"
