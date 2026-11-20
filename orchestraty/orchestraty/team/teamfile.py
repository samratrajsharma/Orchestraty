"""``shadows/<key>.team.json``: what team sync keeps about one repository beside its shadow, such as
the last answer of the anonymous-read probe. Only team sync makes the file, so a solo user never
has one; ``orch uninstall`` removes it with the shadows."""
import json
import os


def read(shadow):
    """The file's contents, {} when it is missing or cannot be read."""
    try:
        with open(shadow.team_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(shadow, **parts):
    """Replace some of the file's top-level parts, keeping the rest; written whole, then renamed
    into place, so a reader never sees half of it."""
    data = read(shadow)
    data.update(parts)
    os.makedirs(os.path.dirname(shadow.team_path), exist_ok=True)
    tmp = shadow.team_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, shadow.team_path)
    return data
