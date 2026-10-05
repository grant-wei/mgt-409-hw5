"""Append-only JSON audit trail for actual PydanticAI execution steps."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = PROJECT_ROOT / "output" / "audit_trail.json"
_LOCK = threading.Lock()


def append_audit_step(
    *,
    run_id: str,
    agent: str,
    step: str,
    node_type: str,
    ticket_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one real execution step without discarding existing records.

    Missing history starts as an empty JSON array. Malformed or non-array
    history raises instead of being overwritten.
    """
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_id": str(uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "agent": agent,
        "ticket_id": ticket_id,
        "step": step,
        "node_type": node_type,
        "detail": detail or {},
    }
    with _LOCK:
        if AUDIT_PATH.exists():
            history = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                raise ValueError(f"Audit history must be a JSON array: {AUDIT_PATH}")
        else:
            history = []
        history.append(record)
        fd, temporary_name = tempfile.mkstemp(
            prefix="audit_trail.", suffix=".tmp", dir=AUDIT_PATH.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(history, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, AUDIT_PATH)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

