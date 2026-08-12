"""Register the accepted PROGRAM R3 migration schema in ``Base.metadata``.

The operational-closure through commercial-tenant tables are currently used by
explicit SQL services rather than ORM entity classes.  Alembic nevertheless
needs their complete SQLAlchemy metadata to verify the accepted migration
chain.  This module replays only the declarative DDL descriptions from the
immutable migration source into metadata; it never opens a connection, runs
DDL, imports application services, or executes a migration environment.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import sqlalchemy as sa

from database import Base


VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_FILES = (
    "0005_field_inspections.py",
    "0006_operational_closure.py",
    "0007_pixel_anomalies.py",
    "0008_irrigation_context.py",
    "0009_yield_map_imports.py",
    "0010_productivity_zones.py",
    "0011_variable_rate_recommendations.py",
    "0012_commercial_tenant_boundary.py",
)


class _MetadataOperations:
    """Minimal Alembic operation recorder that builds SQLAlchemy metadata."""

    def create_table(self, name: str, *items: Any, **_: Any) -> sa.Table:
        existing = Base.metadata.tables.get(name)
        if existing is not None:
            return existing
        return sa.Table(name, Base.metadata, *items)

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[Any],
        **kwargs: Any,
    ) -> sa.Index:
        table = Base.metadata.tables[table_name]
        expressions = [
            table.c[column] if isinstance(column, str) else column
            for column in columns
        ]
        return sa.Index(name, *expressions, **kwargs)

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        **kwargs: Any,
    ) -> None:
        table = Base.metadata.tables[source_table]
        references = [f"{referent_table}.{column}" for column in remote_cols]
        table.append_constraint(
            sa.ForeignKeyConstraint(local_cols, references, name=name, **kwargs)
        )

    def create_unique_constraint(
        self, name: str, table_name: str, columns: list[str], **kwargs: Any
    ) -> None:
        table = Base.metadata.tables[table_name]
        if name not in {constraint.name for constraint in table.constraints}:
            table.append_constraint(sa.UniqueConstraint(*columns, name=name, **kwargs))

    def create_check_constraint(
        self, name: str, table_name: str, condition: str, **kwargs: Any
    ) -> None:
        table = Base.metadata.tables[table_name]
        table.append_constraint(sa.CheckConstraint(condition, name=name, **kwargs))

    def drop_constraint(self, name: str, table_name: str, **_: Any) -> None:
        table = Base.metadata.tables[table_name]
        for constraint in tuple(table.constraints):
            if constraint.name == name:
                table.constraints.remove(constraint)
                return

    def add_column(self, table_name: str, column: sa.Column[Any], **_: Any) -> None:
        table = Base.metadata.tables[table_name]
        if column.name not in table.c:
            table.append_column(column)

    def alter_column(
        self,
        table_name: str,
        column_name: str,
        *,
        type_: Any | None = None,
        nullable: bool | None = None,
        server_default: Any | None = None,
        **_: Any,
    ) -> None:
        """Apply representable column changes; never silently discard DDL.

        ``alembic_version`` is Alembic's own bookkeeping table, deliberately
        absent from application ``Base.metadata``.  The sole accepted
        alteration of that table is recorded but cannot be represented here.
        Every application-table alteration must target a registered column and
        is applied to metadata or fails closed during import.
        """
        if table_name == "alembic_version" and column_name == "version_num":
            return
        table = Base.metadata.tables.get(table_name)
        if table is None or column_name not in table.c:
            raise RuntimeError(
                f"Unsupported migration metadata alteration: {table_name}.{column_name}"
            )
        column = table.c[column_name]
        if type_ is not None:
            column.type = type_
        if nullable is not None:
            column.nullable = nullable
        if server_default is not None:
            column.server_default = sa.DefaultClause(server_default)


def _load_migration(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_metadata_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration metadata source: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def register_program_r3_schema() -> None:
    """Populate metadata once from the accepted, forward-only migrations."""
    if getattr(Base.metadata, "_program_r3_schema_registered", False):
        return
    # Keep this module safe to import directly by qualification tools.  The
    # accepted migrations alter and reference the original core tables, so the
    # corresponding declarative models must be registered before replay.
    from models.crop import CropType  # noqa: F401
    from models.enterprise import Enterprise  # noqa: F401
    from models.field import CropSeason, Field  # noqa: F401
    from models.monitoring import (  # noqa: F401
        Alert,
        NDVIRecord,
        SatelliteIndexRecord,
        ScoutingNote,
        User,
    )
    operations = _MetadataOperations()
    for filename in MIGRATION_FILES:
        module = _load_migration(VERSIONS / filename)
        module.op = operations
        module.upgrade()
    Base.metadata._program_r3_schema_registered = True


register_program_r3_schema()
