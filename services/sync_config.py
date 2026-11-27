from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SYNC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "sync.yaml"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED_COLUMNS = (
    "uuid",
    "created_at",
    "updated_at",
    "deleted_at",
    "user_id",
    "device_id",
)


@dataclass(frozen=True)
class SyncEntity:
    name: str
    table: str
    uuid: str
    created_at: str
    updated_at: str
    deleted_at: str
    user_id: str
    device_id: str
    conflict_columns: tuple[str, ...] = ()
    legacy_server_table: str | None = None
    local_id: str | None = None

    @property
    def metadata_columns(self) -> dict[str, str]:
        return {
            "uuid": self.uuid,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
            "user_id": self.user_id,
            "device_id": self.device_id,
        }


def load_sync_entities(
    config_path: str | Path | None = None,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, SyncEntity]:
    path = Path(config_path) if config_path is not None else DEFAULT_SYNC_CONFIG_PATH
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to load sync manifest {path}: {exc}") from exc

    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("Sync manifest must be a mapping with version: 1")
    definitions = raw.get("entities")
    if not isinstance(definitions, dict) or not definitions:
        raise ValueError("Sync manifest entities must be a non-empty mapping")

    entities: dict[str, SyncEntity] = {}
    for name, definition in definitions.items():
        if not isinstance(name, str) or not _is_identifier(name):
            raise ValueError(f"Invalid sync entity name: {name!r}")
        if not isinstance(definition, dict):
            raise ValueError(f"Sync entity {name!r} must be a mapping")

        fields = {key: definition.get(key) for key in (*_REQUIRED_COLUMNS, "table")}
        for key, value in fields.items():
            if not isinstance(value, str) or not _is_identifier(value):
                raise ValueError(
                    f"Sync entity {name!r} requires a valid {key!r} identifier"
                )

        conflict_columns = definition.get("conflict_columns", [])
        if not isinstance(conflict_columns, list) or any(
            not isinstance(column, str) or not _is_identifier(column)
            for column in conflict_columns
        ):
            raise ValueError(
                f"Sync entity {name!r} conflict_columns must be identifiers"
            )
        legacy_server_table = definition.get("legacy_server_table")
        if legacy_server_table is not None and (
            not isinstance(legacy_server_table, str)
            or not _is_identifier(legacy_server_table)
        ):
            raise ValueError(
                f"Sync entity {name!r} legacy_server_table must be an identifier"
            )
        local_id = definition.get("local_id")
        if local_id is not None and (
            not isinstance(local_id, str) or not _is_identifier(local_id)
        ):
            raise ValueError(f"Sync entity {name!r} local_id must be an identifier")

        entities[name] = SyncEntity(
            name=name,
            table=fields["table"],
            uuid=fields["uuid"],
            created_at=fields["created_at"],
            updated_at=fields["updated_at"],
            deleted_at=fields["deleted_at"],
            user_id=fields["user_id"],
            device_id=fields["device_id"],
            conflict_columns=tuple(conflict_columns),
            legacy_server_table=legacy_server_table,
            local_id=local_id,
        )

    if connection is not None:
        validate_sync_entity_schema(entities, connection)
    return entities


def validate_sync_entity_schema(
    entities: dict[str, SyncEntity], connection: sqlite3.Connection
) -> None:
    for entity in entities.values():
        table_row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (entity.table,),
        ).fetchone()
        if table_row is None:
            raise ValueError(
                f"Sync entity {entity.name!r} table {entity.table!r} does not exist"
            )

        quoted_table = _quote_identifier(entity.table)
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({quoted_table})")
        }
        required = set(entity.metadata_columns.values()) | set(entity.conflict_columns)
        if entity.local_id:
            required.add(entity.local_id)
        missing = sorted(required - columns)
        if missing:
            raise ValueError(
                f"Sync entity {entity.name!r} table {entity.table!r} is missing "
                f"columns: {', '.join(missing)}"
            )
        primary_key_columns = [
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({quoted_table})")
            if int(row[5]) > 0
        ]
        uuid_is_unique = primary_key_columns == [entity.uuid]
        for index_row in connection.execute(f"PRAGMA index_list({quoted_table})"):
            if not int(index_row[2]):
                continue
            index_name = _quote_identifier(str(index_row[1]))
            index_columns = [
                str(index_column[2])
                for index_column in connection.execute(
                    f"PRAGMA index_info({index_name})"
                )
            ]
            if index_columns == [entity.uuid]:
                uuid_is_unique = True
                break
        if not uuid_is_unique:
            raise ValueError(
                f"Sync entity {entity.name!r} UUID column {entity.uuid!r} "
                "must be a unique key"
            )


def _is_identifier(value: str) -> bool:
    return bool(_IDENTIFIER_PATTERN.fullmatch(value))


def _quote_identifier(value: str) -> str:
    if not _is_identifier(value):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return f'"{value}"'
