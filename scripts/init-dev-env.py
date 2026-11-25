"""Initialize a private Compose environment for this checkout."""

import os
import secrets
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    template = (root / ".env.example").read_text(encoding="utf-8")
    values = {"SECRET_KEY": secrets.token_urlsafe(64), "DB_PASSWORD": secrets.token_urlsafe(32)}
    lines = []
    for line in template.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in values and not value:
            line = name + "=" + values.pop(name)
        lines.append(line)
    if values:
        raise SystemExit("Missing blank credential fields in .env.example")

    try:
        fd = os.open(root / ".env", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        print("Using the existing root .env")
        return
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as env:
        env.write("\n".join(lines) + "\n")
    print("Created root .env with fresh local development credentials")


if __name__ == "__main__":
    main()
