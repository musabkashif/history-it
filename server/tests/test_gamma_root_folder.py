"""Gamma root-folder Start/worker fix. Offline only; no provider or register I/O."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.diagnostics import Diagnostics
from app.identity import ROOT_FOLDER, assign_identity, folder_code
from app.orchestrator import Orchestrator
from client_reference.commit_engine import CommitError
from tests.test_workflow import CapturingInspector, MemorySheets, Settings, install_job, manifest, queue_row


def gamma_manifest(names, folder='Scrapbook 4'):
    return [{
        'id': f'id-{item}',
        'name': item,
        'folder_id': 'root-folder-id',
        'folder_name': folder,
        'path': f'/{folder}/{item}',
        'mime_type': 'image/jpeg',
    } for item in names]


def gamma_batch(**extra):
    names = [
        'Gamma_Scrapbook4_001.jpg',
        'Gamma_Scrapbook4_002.jpg',
    ]
    row = {
        'Project': 'Gamma Phi Beta Canadian Scrapbooks',
        'Job ID': 'HIT-4435b42e0c217f99',
        'Batch ID': 'HIT-4435b42e0c217f99-B0001',
        'Box': 'Scrapbook 4',
        'Source Group / Folder': 'Scrapbook 4',
        'Batch Status': 'Authorized / Pending Inspection',
        'Prior Open Item': 'None',
        'Resulting Open Item': 'None',
        'Inspection Attempt': '0',
        'Commit Attempt': '0',
        'Planned Asset Count': str(len(names)),
        'Start Filename': names[0],
        'End Filename': names[-1],
        'Notes': json.dumps({'manifest': gamma_manifest(names)}),
        'Error / Retry Note': '',
    }
    row.update(extra)
    return row


class GammaRootFolderCodeTests(unittest.TestCase):
    def test_valid_gamma_root_manifest_uses_root(self):
        batch = gamma_batch()
        self.assertEqual(folder_code(batch), ROOT_FOLDER)

    def test_f_folder_source_group_still_wins(self):
        batch = queue_row(names=['TriDelta_Year2_Chapters_Box19_F01_001.jpg'])
        self.assertEqual(folder_code(batch), 'F01')

    def test_filename_encoded_f_folder_still_wins_over_drive_group(self):
        names = ['TriDelta_Year2_Chapters_Box31_F04_002.jpg']
        batch = queue_row(
            names=names,
            **{
                'Box': 'Chapters Box 31',
                'Source Group / Folder': '1kivnDriveFolderIdOnly',
                'Notes': json.dumps({'manifest': manifest(names, folder='1kivnDriveFolderIdOnly')}),
            },
        )
        self.assertEqual(folder_code(batch), 'F04')

    def test_ambiguous_or_empty_root_evidence_still_fails(self):
        batch = gamma_batch(Notes=json.dumps({'manifest': []}))
        with self.assertRaisesRegex(CommitError, 'Cannot determine source folder'):
            folder_code(batch)
        mixed = gamma_batch(Notes=json.dumps({
            'manifest': gamma_manifest(['a.jpg'], 'Scrapbook 4') + gamma_manifest(['b.jpg'], 'Other Root'),
        }))
        with self.assertRaisesRegex(CommitError, 'Cannot determine source folder'):
            folder_code(mixed)

    def test_root_folder_can_mint_record_ids(self):
        batch = gamma_batch()
        assets = [{
            'Digital Asset': 'Gamma_Scrapbook4_001.jpg',
            'Assigned Record ID': 'local-1',
            'Action Needed': 'No Adjustment',
            'Production Queue Eligible': 'No',
        }]
        records = [{
            'Proposed Record ID': 'local-1',
            'Record Kind': 'Normal Odyssey',
            'Record Operation': 'Create New',
            'Template': 'Document',
            'Type': '',
            'Metadata Readiness': 'Ready',
            'Boundary State After Record': 'Open',
            'Production Task Count': '0',
        }]
        folder = assign_identity(batch, assets, records, [], [], [], 'None')
        self.assertEqual(folder, ROOT_FOLDER)
        self.assertEqual(records[0]['Proposed Record ID'], 'GPBCS-B4-ROOT-001')
        self.assertEqual(assets[0]['Assigned Record ID'], 'GPBCS-B4-ROOT-001')


class GammaStartWorkerTests(unittest.TestCase):
    def test_valid_gamma_root_reaches_inspection_inputs(self):
        sheets = MemorySheets()
        install_job(sheets, [gamma_batch()])
        inspector = CapturingInspector(error=RuntimeError('stop-after-inputs'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-4435b42e0c217f99')
        self.assertEqual(len(inspector.calls), 1)
        self.assertEqual(inspector.calls[0]['context']['folder_code'], ROOT_FOLDER)
        row = sheets.rows('register', 'Batch Queue')[0]
        self.assertNotEqual(row['Batch Status'], 'Authorized / Pending Inspection')
        self.assertIn('stop-after-inputs', row.get('Error / Retry Note') or '')

    def test_source_folder_failure_persists_visible_status_without_in_progress(self):
        sheets = MemorySheets()
        # Manifest is present so authorization can parse it, but Source Group does
        # not match the root folder_name, so folder_code raises CommitError.
        names = ['Gamma_Scrapbook4_001.jpg', 'Gamma_Scrapbook4_002.jpg']
        broken = gamma_batch(**{
            'Source Group / Folder': 'Wrong Root Label',
            'Planned Asset Count': str(len(names)),
            'Start Filename': names[0],
            'End Filename': names[-1],
            'Notes': json.dumps({'manifest': gamma_manifest(names, 'Scrapbook 4')}),
        })
        install_job(sheets, [broken])
        with tempfile.TemporaryDirectory() as directory:
            inspector = CapturingInspector(error=RuntimeError('must-not-inspect'))
            inspector.diagnostics = Diagnostics(directory)
            Orchestrator(sheets, None, inspector, Settings()).run('HIT-4435b42e0c217f99')
            self.assertEqual(inspector.calls, [])
            row = sheets.rows('register', 'Batch Queue')[0]
            self.assertEqual(row['Batch Status'], 'Blocked - Human Review')
            self.assertEqual(row['Inspection Attempt'], '0')
            self.assertNotEqual(row['Batch Status'], 'Inspection In Progress')
            self.assertIn('Cannot determine source folder', row['Error / Retry Note'])
            self.assertIn('HIT-4435b42e0c217f99-B0001', row['Error / Retry Note'])
            self.assertIn('Diagnostic:', row['Error / Retry Note'])
            self.assertTrue(list(Path(directory).glob('*.json')))

    def test_valid_f_folder_job_still_starts_inspection(self):
        sheets = MemorySheets()
        install_job(sheets, [queue_row()])
        inspector = CapturingInspector(error=RuntimeError('stop'))
        Orchestrator(sheets, None, inspector, Settings()).run('HIT-1')
        self.assertEqual(len(inspector.calls), 1)
        self.assertEqual(inspector.calls[0]['context']['folder_code'], 'F01')


if __name__ == '__main__':
    unittest.main()
