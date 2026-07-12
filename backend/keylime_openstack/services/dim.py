"""DIM trust evidence provider placeholder.

The next phase will integrate openEuler DIM as an additional evidence provider.
Keeping this adapter boundary now avoids baking Keylime-only assumptions into
the OpenStack scheduling decision path.
"""

from __future__ import annotations

from typing import Any


class DimEvidenceProvider:
    provider_name = "dim"

    def collect(self, host: str) -> dict[str, Any]:
        return {
            "host": host,
            "provider": self.provider_name,
            "status": "not_configured",
            "summary": "DIM provider is reserved for the next integration phase.",
        }
