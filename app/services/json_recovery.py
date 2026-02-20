from __future__ import annotations

import ast
import json
import re
from typing import Any


def recover_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        loaded = json.loads(cleaned)
        if not isinstance(loaded, dict):
            msg = "Expected JSON object"
            raise ValueError(msg)
        return loaded
    except json.JSONDecodeError as err:
        literal = ast.literal_eval(cleaned)
        if not isinstance(literal, dict):
            msg = "Expected object from recovery"
            raise ValueError(msg) from err
        return literal