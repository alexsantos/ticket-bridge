"""
status_mapper.py
-----------------
Translates statuses between the internal "common vocabulary" (e.g. 'new',
'in_progress', 'waiting_third_party', 'resolved', 'closed') and the
vocabulary specific to each external system (e.g. 'Open' in system A,
'NEW' in system B).

The mapping is configured in `systems.status_mapping` (JSON:
internal_vocabulary -> external_vocabulary). This avoids hardcoding
per-system statuses in code - adding a new system is just a matter of
filling in this configuration.
"""
import logging

logger = logging.getLogger(__name__)

# "Canonical" internal vocabulary. New systems must map their own statuses
# to one of these values.
INTERNAL_VOCABULARY = {
    "new",
    "in_progress",
    "waiting_third_party",
    "resolved",
    "closed",
}


def external_to_internal(status_mapping: dict[str, str], external_status: str) -> str:
    """
    Converts a status in an external system's vocabulary to the internal
    vocabulary. status_mapping is {internal: external}, so we invert the
    lookup.

    If there's no known mapping, returns the original value lowercased and
    logs a warning - we prefer not to lose information over failing.
    """
    inverted = {v: k for k, v in status_mapping.items()}
    if external_status in inverted:
        return inverted[external_status]

    logger.warning(
        "External status '%s' has no known mapping; using literal value.", external_status
    )
    return external_status.lower()


def internal_to_external(status_mapping: dict[str, str], internal_status: str) -> str:
    """Converts a status from the internal vocabulary to a destination system's vocabulary."""
    if internal_status in status_mapping:
        return status_mapping[internal_status]

    logger.warning(
        "Internal status '%s' has no mapping for this system; using literal value.",
        internal_status,
    )
    return internal_status
