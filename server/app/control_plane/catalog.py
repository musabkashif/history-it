"""Identifiers recorded for staging jobs.

Only values documented for these job ids are present. Register URLs are not in
this repository, so these records do not claim completion. A later register
import can add evidence.
"""

KNOWN_STAGING_JOBS = (
    {
        'job_id': 'HIT-ea97b58c8e4a8c58',
        'project_name': 'Tri Delta Chapters',
        'box_label': 'Chapters Box 31',
        'folder_name': 'F04',
        'asset_count': 39,
        'batch_count': 2,
        'completed_batch_count': 2,
        'last_batch_asset_count': 19,
        'deployed_release': '2044707',
        'message': (
            'Known staging job. The folder had 39 assets and both batches were observed committed '
            'on release 2044707. The register URL is not in this repository, so completion is not claimed.'
        ),
    },
    {
        'job_id': 'HIT-b646d242c6175cdb',
        'message': 'Known staging job id from QC Test 02. Register URL and completion evidence are not in this repository.',
    },
    {
        'job_id': 'HIT-36381173e12dba3e',
        'message': 'Known staging job id from QC Test 03. Register URL and completion evidence are not in this repository.',
    },
)
