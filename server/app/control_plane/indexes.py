"""Index specifications applied when a control database connects.

Tests and a fake database can apply the same list. Nothing here opens a
network connection.
"""

INDEX_SPECS = (
    {'collection': 'workspaces', 'keys': [('workspace_id', 1)], 'unique': True, 'name': 'uniq_workspace_id'},
    {'collection': 'jobs', 'keys': [('job_id', 1)], 'unique': True, 'name': 'uniq_job_id'},
    {'collection': 'jobs', 'keys': [('project_name', 1)], 'unique': False, 'name': 'job_project_name'},
    {'collection': 'jobs', 'keys': [('box_label', 1)], 'unique': False, 'name': 'job_box_label'},
    {'collection': 'jobs', 'keys': [('job_status', 1)], 'unique': False, 'name': 'job_status'},
    {'collection': 'jobs', 'keys': [('created_at', 1)], 'unique': False, 'name': 'job_created_at'},
    {'collection': 'jobs', 'keys': [('updated_at', 1)], 'unique': False, 'name': 'job_updated_at'},
    {'collection': 'jobs', 'keys': [('workspace_id', 1), ('job_id', 1)], 'unique': False, 'name': 'job_workspace'},
    {'collection': 'folders', 'keys': [('job_id', 1), ('folder_id', 1)], 'unique': True, 'name': 'uniq_job_folder'},
    {'collection': 'folders', 'keys': [('workspace_id', 1), ('job_id', 1)], 'unique': False, 'name': 'folder_workspace_job'},
    {'collection': 'batches', 'keys': [('job_id', 1), ('batch_id', 1)], 'unique': True, 'name': 'uniq_job_batch'},
    {'collection': 'batches', 'keys': [('workspace_id', 1), ('job_id', 1)], 'unique': False, 'name': 'batch_workspace_job'},
    {'collection': 'job_events', 'keys': [('event_id', 1)], 'unique': True, 'name': 'uniq_event_id'},
    {'collection': 'job_events', 'keys': [('workspace_id', 1), ('job_id', 1), ('timestamp', 1)], 'unique': False, 'name': 'event_workspace_job_time'},
    {'collection': 'checkpoints', 'keys': [('job_id', 1), ('checkpoint_version', 1)], 'unique': True, 'name': 'uniq_job_checkpoint_version'},
    {'collection': 'checkpoints', 'keys': [('job_id', 1), ('created_at', 1)], 'unique': False, 'name': 'checkpoint_job_time'},
    {'collection': 'register_snapshots', 'keys': [('job_id', 1), ('captured_at', 1)], 'unique': False, 'name': 'snapshot_job_time'},
)


def index_names():
    return [item['name'] for item in INDEX_SPECS]
