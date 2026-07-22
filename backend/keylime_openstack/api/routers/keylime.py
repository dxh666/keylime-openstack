"""Keylime diagnostic API routes."""

from __future__ import annotations

from fastapi import APIRouter

from keylime_openstack.services.keylime_gate import keylime_only_check

router = APIRouter()


@router.get("/keylime/check")
def keylime_check(
    hosts: str = "",
    failures_only: bool = False,
) -> dict[str, object]:
    return keylime_only_check(
        hosts=hosts,
        failures_only=failures_only,
        include_non_keylime=True,
    )
