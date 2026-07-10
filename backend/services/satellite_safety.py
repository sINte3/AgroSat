"""Pure credential and provenance guards for satellite write paths."""

REAL_SATELLITE_SOURCE = "Sentinel-2"
MOCK_SATELLITE_SOURCE = "Mock/Dev"
_UNSAFE_MARKERS = ("mock", "dev", "test", "synthetic")


class SatelliteConfigurationError(RuntimeError):
    pass


class SatelliteProvenanceError(RuntimeError):
    pass


def validate_credentials(client_id, client_secret):
    values = (client_id, client_secret)
    present = [isinstance(value, str) and bool(value.strip()) for value in values]
    if any(present) and not all(present):
        raise SatelliteConfigurationError("Sentinel Hub credentials are incomplete")
    if not all(present):
        raise SatelliteConfigurationError("Complete Sentinel Hub credentials are required")
    if any(value != value.strip() for value in values):
        raise SatelliteConfigurationError("Sentinel Hub credentials are malformed")
    return client_id, client_secret


def credentials_are_missing(client_id, client_secret):
    try:
        validate_credentials(client_id, client_secret)
    except SatelliteConfigurationError:
        return True
    return False


def is_unsafe_provenance(source):
    if not isinstance(source, str) or not source.strip():
        return True
    normalized = source.strip().casefold()
    return any(marker in normalized for marker in _UNSAFE_MARKERS)


def require_real_provenance(source):
    if not isinstance(source, str) or not source.strip():
        raise SatelliteProvenanceError("Satellite provenance is required")
    if is_unsafe_provenance(source) or source.strip().casefold() != REAL_SATELLITE_SOURCE.casefold():
        raise SatelliteProvenanceError("Unsafe satellite provenance")
    return REAL_SATELLITE_SOURCE


def require_payload_provenance(payload, key="satellite"):
    if not isinstance(payload, dict):
        raise SatelliteProvenanceError("Satellite payload provenance is required")
    return require_real_provenance(payload.get(key))


def validate_batch_provenance(records, key="satellite"):
    records = list(records)
    for record in records:
        require_payload_provenance(record, key=key)
    return records
