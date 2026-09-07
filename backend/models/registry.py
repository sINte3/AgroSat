"""Canonical ORM metadata registry used by Alembic and qualification tools."""

from models.enterprise import Enterprise  # noqa: F401
from models.crop import CropType  # noqa: F401
from models.field import Field, CropSeason  # noqa: F401
from models.monitoring import Alert, NDVIRecord, SatelliteIndexRecord, ScoutingNote, User  # noqa: F401
from models.program_r3_schema import register_program_r3_schema
from models.autonomous_monitoring import register_autonomous_monitoring

register_program_r3_schema()
register_autonomous_monitoring()

from models.closed_loop_agronomy import register_closed_loop_agronomy

register_closed_loop_agronomy()
