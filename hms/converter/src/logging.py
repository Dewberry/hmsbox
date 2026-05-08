#!/usr/bin/env python3

import json

# import shlex
# import subprocess
import sys
from datetime import datetime, timezone


def log_json(level: str, message: str, **fields: object) -> None:
    # Keep only ts/level/message at the top level.
    # When extra fields are provided, encode them into the message string.
    if fields:
        message_text = json.dumps(
            {"text": str(message), **fields}, separators=(",", ":"), default=str
        )
    else:
        message_text = str(message)

    payload: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": str(level).upper(),
        "message": message_text,
    }
    output = json.dumps(payload, separators=(",", ":"))
    if str(payload.get("level", "")).upper() == "ERROR":
        print(output, file=sys.stderr)
    else:
        print(output)
