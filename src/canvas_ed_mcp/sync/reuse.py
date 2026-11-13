"""Private, optional receipts for verified content; never ownership evidence."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from ..cancellation import check_cancelled
from .model import validate_output_relative_path
from .storage import sha256_file


class ContentReceipts:
    def __init__(self, cache_dir, output_root, course_root):
        self.directory = Path(cache_dir) / "content-receipts-v1"
        self.scope = (str(Path(output_root).resolve()), str(course_root))

    def _path(self, item):
        key = json.dumps(
            (self.scope, item.key.identity_tuple, item.signature), separators=(",", ":")
        )
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def record(self, item, relative, digest, size=None):
        check_cancelled()
        path = self._path(item)
        payload = {
            "version": 1,
            "relative": str(relative),
            "sha256": digest,
            "size": size,
        }
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=self.directory, delete=False, encoding="utf-8"
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(temporary, path)
        except OSError:
            pass  # Receipts are an optimization; state remains authoritative.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self, item):
        try:
            path = self._path(item)
            if path.is_symlink() or path.stat().st_size > 4096:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != {
                "version",
                "relative",
                "sha256",
                "size",
            }:
                return None
            if (
                data["version"] != 1
                or not isinstance(data["sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", data["sha256"])
            ):
                return None
            validate_output_relative_path(PurePosixPath(data["relative"]))
            if data["size"] is not None and (
                type(data["size"]) is not int or data["size"] < 0
            ):
                return None
            return data
        except (OSError, ValueError, TypeError):
            return None

    def find(self, receipt, index):
        candidates = (
            index.files
            if receipt["size"] is None
            else index._by_size.get(receipt["size"], ())
        )
        # Try the last location first; a move is matched by bytes, never name alone.
        candidates = sorted(
            candidates,
            key=lambda entry: str(entry.output_relative_path) != receipt["relative"],
        )
        for entry in candidates:
            check_cancelled()
            try:
                if sha256_file(index._absolute(entry)) == receipt["sha256"]:
                    return entry
            except (OSError, ValueError):
                pass
        return None
