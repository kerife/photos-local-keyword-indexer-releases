"""Private, immutable catalogue snapshots with bounded external sorting."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import heapq
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unicodedata

from .adapters import SelectedPhoto
from .manifest import ManifestError, _atomic_private_write, _reject_symlinked_ancestors

CHUNK_SIZE = 200
_MERGE_FAN_IN = 32
# JSON can use twelve ASCII bytes per non-BMP identifier character.
_MAX_FILE_BYTES = 2 * 1024 * 1024


class InventoryError(ValueError):
    """The private inventory is unavailable or unsafe to consume."""


def _directory(path: Path, *, private: bool = True) -> None:
    try:
        _reject_symlinked_ancestors(path)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or (private and stat.S_IMODE(info.st_mode) != 0o700)):
            raise InventoryError("inventory directory is unsafe")
    except (OSError, ManifestError) as error:
        raise InventoryError("inventory directory is unavailable") from error


def _read_json(path: Path) -> object:
    _directory(path.parent)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > _MAX_FILE_BYTES):
                raise InventoryError("inventory file is unsafe")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                payload = handle.read(_MAX_FILE_BYTES + 1)
            after = os.fstat(descriptor)
            if (len(payload) > _MAX_FILE_BYTES or after.st_nlink != 1
                    or (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise InventoryError("inventory file changed")
            return json.loads(payload)
        finally:
            os.close(descriptor)
    except (OSError, UnicodeError, ValueError) as error:
        raise InventoryError("inventory file is invalid") from error
    finally:
        os.close(directory_fd)


def _record(photo: SelectedPhoto | None) -> dict[str, str] | None:
    if not isinstance(photo, SelectedPhoto):
        return None
    identifier, date = photo.local_id, photo.creation_date
    if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 512
            or any(unicodedata.category(char).startswith("C") for char in identifier)
            or not isinstance(date, datetime)):
        return None
    if date.tzinfo is not None:
        date = date.astimezone(timezone.utc).replace(tzinfo=None)
    return {"local_id": str(identifier), "creation_date": date.isoformat(timespec="microseconds")}


def _sort_key(record: dict[str, str]) -> tuple:
    date = datetime.fromisoformat(record["creation_date"])
    return (-date.year, -date.month, -date.day, -date.hour, -date.minute, -date.second,
            -date.microsecond, record["local_id"])


def _write_json(path: Path, value: object) -> None:
    _atomic_private_write(path, json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def _write_run(path: Path, records: Iterable[dict[str, str]]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")


def _sorted_records(staging: Path, records: Iterable[SelectedPhoto | None], counts: list[int],
                    *, prefix: str = "dates", key=_sort_key):
    """Only one 200-record buffer and at most 32 merge cursors reside in memory."""
    count = 0
    buffer: list[dict[str, str]] = []
    for photo in records:
        record = _record(photo)
        if record is None:
            counts[1] += 1
            continue
        counts[0] += 1
        buffer.append(record)
        if len(buffer) == CHUNK_SIZE:
            _write_run(staging / f"{prefix}-0-{count}", sorted(buffer, key=key))
            count += 1
            buffer.clear()
    if buffer:
        _write_run(staging / f"{prefix}-0-{count}", sorted(buffer, key=key))
        count += 1
    level = 0
    while count > 1:
        output_count = 0
        for start in range(0, count, _MERGE_FAN_IN):
            paths = [staging / f"{prefix}-{level}-{index}" for index in range(start, min(count, start + _MERGE_FAN_IN))]
            with ExitStack() as stack:
                handles = [stack.enter_context(path.open(encoding="utf-8")) for path in paths]
                rows = (map(json.loads, handle) for handle in handles)
                _write_run(staging / f"{prefix}-{level + 1}-{output_count}", heapq.merge(*rows, key=key))
            for path in paths:
                path.unlink()
            output_count += 1
        level += 1
        count = output_count
    if count:
        path = staging / f"{prefix}-{level}-0"
        with path.open(encoding="utf-8") as handle:
            yield from map(json.loads, handle)
        path.unlink()


def _unique_records(staging: Path, records: Iterable[SelectedPhoto | None], counts: list[int]):
    previous = None
    for record in _sorted_records(staging, records, counts, prefix="ids", key=lambda row: row["local_id"]):
        if record["local_id"] == previous:
            raise InventoryError("inventory contains duplicate identifiers")
        previous = record["local_id"]
        yield SelectedPhoto(record["local_id"], datetime.fromisoformat(record["creation_date"]))


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _node_hash(left: str, right: str) -> str:
    return hashlib.sha256(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def _attach_proofs(staging: Path, count: int) -> str:
    """Build a disk Merkle tree, then attach a small authentication path per page."""
    if not count:
        return _hash([])
    path = staging / "hash-0"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        for index in range(count):
            handle.write(_hash(_read_json(staging / f"chunk-{index:08d}.json")) + "\n")
    width, levels = count, 0
    while width > 1:
        descriptor = os.open(staging / f"hash-{levels + 1}", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with path.open() as source, os.fdopen(descriptor, "w") as target:
            for left in source:
                right = source.readline() or left
                target.write(_node_hash(left.strip(), right.strip()) + "\n")
        width = (width + 1) // 2
        levels += 1
        path = staging / f"hash-{levels}"
    root = path.read_text().strip()
    with ExitStack() as stack:
        handles = [stack.enter_context((staging / f"hash-{level}").open("rb")) for level in range(levels)]
        for index in range(count):
            position, width, proof = index, count, []
            for handle in handles:
                sibling = position ^ 1
                if sibling >= width:
                    sibling = position
                handle.seek(sibling * 65)
                proof.append(handle.read(64).decode("ascii"))
                position //= 2
                width = (width + 1) // 2
            chunk_path = staging / f"chunk-{index:08d}.json"
            value = _read_json(chunk_path)
            value["proof"] = proof
            _write_json(chunk_path, value)
    for level in range(levels + 1):
        (staging / f"hash-{level}").unlink()
    return root


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    path: Path
    total_count: int
    invalid_count: int
    chunk_count: int
    digest: str
    root_hash: str

    @classmethod
    def create(cls, path: Path, records: Iterable[SelectedPhoto | None]) -> InventorySnapshot:
        path = Path(path).absolute()
        _directory(path.parent, private=False)
        if path.exists() or path.is_symlink():
            raise InventoryError("inventory already exists")
        staging = Path(tempfile.mkdtemp(prefix=".inventory-", dir=path.parent))
        try:
            counts = [0, 0]
            chunk_count = 0
            buffer: list[dict[str, str]] = []
            unique = _unique_records(staging, records, counts)
            for record in _sorted_records(staging, unique, [0, 0]):
                buffer.append(record)
                if len(buffer) == CHUNK_SIZE:
                    _write_json(staging / f"chunk-{chunk_count:08d}.json", {"index": chunk_count, "records": buffer})
                    chunk_count += 1
                    buffer.clear()
            if buffer:
                _write_json(staging / f"chunk-{chunk_count:08d}.json", {"index": chunk_count, "records": buffer})
                chunk_count += 1
            root_hash = _attach_proofs(staging, chunk_count)
            metadata = {"version": 1, "total_count": counts[0], "invalid_count": counts[1],
                        "chunk_count": chunk_count, "root_hash": root_hash}
            digest = _hash(metadata)
            _write_json(staging / "snapshot.json", {**metadata, "digest": digest})
            _directory(path.parent, private=False)
            if path.exists() or path.is_symlink():
                raise InventoryError("inventory already exists")
            os.rename(staging, path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return cls(path, counts[0], counts[1], chunk_count, digest, root_hash)
        except OSError as error:
            raise InventoryError("inventory could not be saved") from error
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @classmethod
    def load(cls, path: Path) -> InventorySnapshot:
        path = Path(path).absolute()
        value = _read_json(path / "snapshot.json")
        if (type(value) is not dict
                or set(value) != {"version", "total_count", "invalid_count", "chunk_count", "root_hash", "digest"}
                or type(value["version"]) is not int or value["version"] != 1
                or any(type(value[field]) is not int or value[field] < 0
                       for field in ("total_count", "invalid_count", "chunk_count"))
                or value["chunk_count"] != (value["total_count"] + CHUNK_SIZE - 1) // CHUNK_SIZE
                or not _valid_hash(value["root_hash"]) or not _valid_hash(value["digest"])
                or value["digest"] != _hash({k: v for k, v in value.items() if k != "digest"})):
            raise InventoryError("inventory metadata is invalid")
        return cls(path, value["total_count"], value["invalid_count"], value["chunk_count"],
                   value["digest"], value["root_hash"])

    def page(self, index: int) -> tuple[SelectedPhoto, ...]:
        if type(index) is not int or not 0 <= index < self.chunk_count:
            raise InventoryError("inventory page is out of range")
        value = _read_json(self.path / f"chunk-{index:08d}.json")
        expected = min(CHUNK_SIZE, self.total_count - index * CHUNK_SIZE)
        if (type(value) is not dict or set(value) != {"index", "records", "proof"}
                or type(value["index"]) is not int or value["index"] != index
                or type(value["records"]) is not list or len(value["records"]) != expected):
            raise InventoryError("inventory page is invalid")
        proof = value["proof"]
        width, position = self.chunk_count, index
        if type(proof) is not list or len(proof) != (width - 1).bit_length():
            raise InventoryError("inventory proof is invalid")
        digest = _hash({"index": index, "records": value["records"]})
        for sibling in proof:
            if not _valid_hash(sibling):
                raise InventoryError("inventory proof is invalid")
            digest = _node_hash(sibling, digest) if position % 2 else _node_hash(digest, sibling)
            position //= 2
        if digest != self.root_hash:
            raise InventoryError("inventory page changed")
        photos: list[SelectedPhoto] = []
        previous = None
        for record in value["records"]:
            try:
                if type(record) is not dict or set(record) != {"local_id", "creation_date"}:
                    raise ValueError
                photo = SelectedPhoto(record["local_id"], datetime.fromisoformat(record["creation_date"]))
                if _record(photo) != record:
                    raise ValueError
                current = _sort_key(record)
                if previous is not None and previous > current:
                    raise ValueError
                previous = current
            except (TypeError, ValueError) as error:
                raise InventoryError("inventory record is invalid") from error
            photos.append(photo)
        return tuple(photos)

    def iter_from(self, offset: int = 0) -> Iterator[SelectedPhoto]:
        if type(offset) is not int or not 0 <= offset <= self.total_count:
            raise InventoryError("inventory offset is out of range")
        for index in range(offset // CHUNK_SIZE, self.chunk_count):
            page = self.page(index)
            yield from page[offset % CHUNK_SIZE:] if index == offset // CHUNK_SIZE else page


def _valid_hash(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
