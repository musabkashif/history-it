"""Control-plane errors. Messages must stay free of connection strings."""


class ControlPlaneError(Exception):
    pass


class ControlPlaneDisabled(ControlPlaneError):
    pass


class ControlPlaneUnavailable(ControlPlaneError):
    pass


class NotFound(ControlPlaneError):
    pass


class WorkspaceIsolationError(ControlPlaneError):
    pass


class Conflict(ControlPlaneError):
    pass


class NotAllowed(ControlPlaneError):
    pass


class LeaseHeld(ControlPlaneError):
    pass


class DuplicateRecord(ControlPlaneError):
    pass
