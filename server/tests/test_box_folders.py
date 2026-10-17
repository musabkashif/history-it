import unittest

from app.box_folders import (
    detect_ambiguous_f_folders,
    natural_f_key,
    select_authorized_sources,
)
from app.models import JobCreate, Source
from app.orchestrator import Orchestrator
from tests.test_stage import MemorySheets


def source(folder_id, name, path):
    return Source(id=folder_id + '-' + name, name=name, folder_id=folder_id, folder_name=path.split('/')[-2], path=path, mime_type='image/jpeg')


class BoxFolderTests(unittest.TestCase):
    def test_parent_box_keeps_f_folders_in_natural_order_and_skips_siblings(self):
        sources = [
            source('del', 'TriDelta_Year2_Chapters_Box19_F02_001.jpg', 'Chapters Box 19/Delhivery/F02/TriDelta_Year2_Chapters_Box19_F02_001.jpg'),
            source('del-root', 'note.jpg', 'Chapters Box 19/Delhivery/note.jpg'),
            source('f06', 'f06.jpg', 'Chapters Box 19/F06/f06.jpg'),
            source('f01b', 'f01b.jpg', 'Chapters Box 19/F01/f01b.jpg'),
            source('f01a', 'f01a.jpg', 'Chapters Box 19/F01/f01a.jpg'),
            source('f03', 'f03.jpg', 'Chapters Box 19/F03/f03.jpg'),
            source('f10', 'f10.jpg', 'Chapters Box 19/F10/f10.jpg'),
            source('loose', 'loose.jpg', 'Chapters Box 19/loose.jpg'),
        ]
        selected = select_authorized_sources(sources)
        self.assertEqual([item.name for item in selected], ['f01a.jpg', 'f01b.jpg', 'f03.jpg', 'f06.jpg', 'f10.jpg'])
        self.assertEqual([item.folder_id for item in selected], ['f01a', 'f01b', 'f03', 'f06', 'f10'])

    def test_natural_order_places_f10_after_f02(self):
        names = ['F10', 'F2', 'F01', 'F02']
        self.assertEqual(sorted(names, key=natural_f_key), ['F01', 'F02', 'F2', 'F10'])

    def test_more_than_six_f_folders_are_discovered(self):
        sources = [
            source(f'f{n:02d}', f'f{n:02d}.jpg', f'Chapters Box 19/F{n:02d}/f{n:02d}.jpg')
            for n in range(1, 9)
        ]
        selected = select_authorized_sources(sources)
        self.assertEqual([item.folder_name for item in selected], [f'F{n:02d}' for n in range(1, 9)])

    def test_a_single_authorized_folder_is_unchanged(self):
        sources = [
            source('f01', 'b.jpg', '/b.jpg'),
            source('f01', 'a.jpg', 'F01/a.jpg'),
        ]
        self.assertEqual(select_authorized_sources(sources), sources)

    def test_a_parent_without_f_folders_is_refused(self):
        sources = [source('other', 'a.jpg', 'Chapters Box 19/Notes/a.jpg')]
        with self.assertRaisesRegex(ValueError, 'F01, F02'):
            select_authorized_sources(sources)

    def test_ambiguous_f_identities_are_reported(self):
        conflicts = detect_ambiguous_f_folders(['F01', 'F1', 'F02'])
        self.assertTrue(any('ambiguous' in item for item in conflicts))
        sources = [
            source('a', 'a.jpg', 'Chapters Box 19/F01/a.jpg'),
            source('b', 'b.jpg', 'Chapters Box 19/F1/b.jpg'),
        ]
        with self.assertRaisesRegex(ValueError, 'Ambiguous F-folder'):
            select_authorized_sources(sources)

    def test_create_queues_the_box_children_in_folder_order(self):
        class Drive:
            def enumerate(self, root):
                self.root = root
                return select_authorized_sources([
                    source('f02', 'f02.jpg', 'Chapters Box 19/F02/f02.jpg'),
                    source('del', 'copy.jpg', 'Chapters Box 19/Delhivery/copy.jpg'),
                    source('f01', 'f01.jpg', 'Chapters Box 19/F01/f01.jpg'),
                    source('f07', 'f07.jpg', 'Chapters Box 19/F07/f07.jpg'),
                ])

        class Settings:
            job_index_spreadsheet_id = 'index'
            batch_size = 10

        sheets = MemorySheets()
        drive = Drive()
        Orchestrator(sheets, drive, None, Settings()).create(JobCreate(
            project='Tri Delta Chapters', source_folder_id='box-19', spreadsheet_id='register', batch_size=10, box='Chapters Box 19',
        ))
        rows = sheets.rows('register', 'Batch Queue')
        self.assertEqual(drive.root, 'box-19')
        self.assertEqual([row['Source Group / Folder'] for row in rows], ['F01', 'F02', 'F07'])
        self.assertEqual(rows[0]['Prior Open Item'], 'None')
        self.assertEqual(rows[1]['Prior Open Item'], '')
        self.assertEqual([row['Start Filename'] for row in rows], ['f01.jpg', 'f02.jpg', 'f07.jpg'])
        self.assertNotIn('copy.jpg', [row['Start Filename'] for row in rows])
        state = Orchestrator(sheets, drive, None, Settings()).state(rows[0]['Job ID'])
        self.assertEqual(state['discovered_folders'], ['F01', 'F02', 'F07'])
