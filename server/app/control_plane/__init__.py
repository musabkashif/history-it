"""MongoDB control plane.

Google Sheets remains the authoritative register. This package records
workspaces, jobs, folders, batches, checkpoints, events, and register
snapshots. It does not inspect images, commit registers, or rewrite them.

The FastAPI app and the orchestrator are synchronous, so this package uses
the synchronous PyMongo driver. A second database library is not introduced.
"""
