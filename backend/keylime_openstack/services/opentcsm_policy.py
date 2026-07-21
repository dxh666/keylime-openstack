"""OpenTCSM policy authorization and dynamic measurement helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keylime_openstack.config import Settings


SAFE_AUTH_REF = re.compile(r"^[A-Za-z0-9_.-]+$")
OPEN_TCSM_DMEASURE_OBJECTS = ("kernel_section", "syscall_table", "idt_table")


@dataclass(frozen=True)
class OpenTcsmAuthMaterial:
    """Signing material used by OpenTCSM policy update commands.

    The private key is intentionally kept out of the database and API responses.
    It is loaded from a root-owned file at deployment time and only passed to the
    remote OpenTCSM command task.
    """

    ref: str
    uid: str
    auth_type: int
    private_key: str
    public_key: str

    @property
    def signing_key(self) -> str:
        return f"{self.private_key}{self.public_key}"

    @property
    def public_fingerprint(self) -> str:
        return hashlib.sha256(f"{self.uid}:{self.public_key}".encode("utf-8")).hexdigest()

    def playbook_vars(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "uid": self.uid,
            "auth_type": self.auth_type,
            "private_key": self.private_key,
            "public_key": self.public_key,
        }

    def metadata(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "uid": self.uid,
            "auth_type": self.auth_type,
            "public_key_sha256": self.public_fingerprint,
        }


def load_opentcsm_auth_material(settings: Settings, ref: str | None = None) -> OpenTcsmAuthMaterial:
    """Load a named OpenTCSM signing material file from the protected key dir."""

    selected = (ref or settings.opentcsm_default_dynamic_auth_ref).strip()
    if not selected or not SAFE_AUTH_REF.fullmatch(selected):
        raise RuntimeError(f"invalid OpenTCSM auth material reference: {selected!r}")
    key_dir = Path(settings.opentcsm_key_dir).resolve()
    path = (key_dir / f"{selected}.json").resolve()
    if path.parent != key_dir:
        raise RuntimeError(f"invalid OpenTCSM auth material path: {selected!r}")
    if not path.is_file():
        raise RuntimeError(
            "OpenTCSM authorization material is not configured: "
            f"{path}. Create a root-owned 0600 JSON file with uid/private_key/public_key."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid OpenTCSM auth material JSON: {path}") from exc

    uid = str(data.get("uid") or selected).strip()
    private_key = _normalize_hex(data.get("private_key"), "private_key")
    public_key = _normalize_hex(data.get("public_key"), "public_key")
    auth_type = int(data.get("auth_type") or 1)
    if auth_type not in (1, 2, 3):
        raise RuntimeError(f"invalid OpenTCSM auth_type in {path}: {auth_type}")
    if not uid:
        raise RuntimeError(f"OpenTCSM auth material {path} has an empty uid")
    return OpenTcsmAuthMaterial(
        ref=selected,
        uid=uid,
        auth_type=auth_type,
        private_key=private_key,
        public_key=public_key,
    )


def normalize_dmeasure_objects(value: Any) -> list[str]:
    """Return a stable, validated list of OpenTCSM environment measurement objects."""

    raw_items = value if isinstance(value, list) else OPEN_TCSM_DMEASURE_OBJECTS
    normalized: list[str] = []
    for item in raw_items:
        name = str(item or "").strip()
        if not name:
            continue
        if name not in OPEN_TCSM_DMEASURE_OBJECTS:
            raise ValueError(f"unsupported OpenTCSM dynamic measurement object: {name}")
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ValueError("OpenTCSM dynamic measurement object list cannot be empty")
    return normalized


def parse_dmeasure_policy(text: str) -> list[dict[str, Any]]:
    """Parse get_dmeasure_policy output into object/interval records."""

    items: dict[int, dict[str, Any]] = {}
    for match in re.finditer(r"item index:\s*(\d+)", text):
        index = int(match.group(1))
        items.setdefault(index, {"index": index})
    for match in re.finditer(r"\[(\d+)\]\.be_type:\s*(0x[0-9A-Fa-f]+|\d+)", text):
        items.setdefault(int(match.group(1)), {"index": int(match.group(1))})["be_type"] = int(
            match.group(2), 0
        )
    for match in re.finditer(r"\[(\d+)\]\.be_interval_milli:\s*(0x[0-9A-Fa-f]+|\d+)", text):
        items.setdefault(int(match.group(1)), {"index": int(match.group(1))})[
            "interval_milli"
        ] = int(match.group(2), 0)
    for match in re.finditer(r"\[(\d+)\]\.object:\s*(.+)", text):
        items.setdefault(int(match.group(1)), {"index": int(match.group(1))})["object"] = (
            match.group(2).strip()
        )
    return [
        item
        for _, item in sorted(items.items())
        if item.get("object")
    ]


def _normalize_hex(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"OpenTCSM auth material is missing {label}")
    if text.startswith(("0x", "0X")):
        text = text[2:]
    if not re.fullmatch(r"[0-9A-Fa-f]+", text):
        raise RuntimeError(f"OpenTCSM auth material {label} must be a hex string")
    return text.upper()
