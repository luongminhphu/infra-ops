"""Registry of connector types. Connectors self-register via :func:`register`."""

from __future__ import annotations

from app.core.connectors.base import AUTH_TYPES, ConnectorType

_registry: dict[str, ConnectorType] = {}


def register[T: type[ConnectorType]](cls: T) -> T:
    instance = cls()
    if instance.system_type in _registry:
        raise ValueError(f"Connector {instance.system_type!r} already registered")
    unknown = [a for a in instance.auth_types if a not in AUTH_TYPES]
    if unknown:
        raise ValueError(f"Connector {instance.system_type!r} uses unknown auth types {unknown}")
    _registry[instance.system_type] = instance
    return cls


def get_connector(system_type: str) -> ConnectorType | None:
    return _registry.get(system_type)


def all_connectors() -> list[ConnectorType]:
    return list(_registry.values())


def load_builtin_connectors() -> None:
    """Import built-in connector modules so they register themselves."""
    from app.core.connectors import active_directory, biostar, exchange, msgraph, sdp, smtp, teams  # noqa: F401
