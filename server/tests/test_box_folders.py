import unittest

from app.box_folders import select_authorized_sources
from app.models import JobCreate, Source
from app.orchestrator import Orchestrator
from tests.test_stage import MemorySheets


def source(folder_id, name, path):
    return Source(id=folder_id + '-' + name, name=name, folder_id=folder_id, folder_name=path.split('/')[-2], path=path, mime_type='image/jpeg')


class BoxFolderTests(unittest.TestCase):
    def test_parent_box_keeps_f01_through_f06_and_skips_delhivery(self):
        sources = [
            source('del', 'TriDelta_Year2_Chapters_Box19_F02_001.jpg', 'Chapters Box 19/Delhivery/F02/TriDelta_Year2_Chapters_Box19_F02_001.jpg'),
            source('del-root', 'note.jpg', 'Chapters Box 19/Delhivery/note.jpg'),
            source('f06', 'f06.jpg', 'Chapters Box 19/F06/f06.jpg'),
            source('f01b', 'f01b.jpg', 'Chapters Box 19/F01/f01b.jpg'),
            source('f01a', 'f01a.jpg', 'Chapters Box 19/F01/f01a.jpg'),
            source('f03', 'f03.jpg', 'Chapters Box 19/F03/f03.jpg'),
            source('loose', 'loose.jpg', 'Chapters Box 19/loose.jpg'),
        ]
        selected = select_authorized_sources(sources)
        self.assertEqual([item.name for item in selected], ['f01a.jpg', 'f01b.jpg', 'f03.jpg', 'f06.jpg'])
        self.assertEqual([item.folder_id for item in selected], ['f01a', 'f01b', 'f03', 'f06'])

    def test_a_single_authorized_folder_is_unchanged(self):
        sources = [
            source('f01', 'b.jpg', '/b.jpg'),
            source('f01', 'a.jpg', 'F01/a.jpg'),
        ]
        self.assertEqual(select_authorized_sources(sources), sources)

    def test_a_parent_without_f01_through_f06_is_refused(self):
        sources = [source('other', 'a.jpg', 'Chapters Box 19/Notes/a.jpg')]
        with self.assertRaisesRegex(ValueError, 'F01 through F06'):
            select_authorized_sources(sources)

    def test_create_queues_the_box_children_in_folder_order(self):
        class Drive:
            def enumerate(self, root):
                self.root = root
                return select_authorized_sources([
                    source('f02', 'f02.jpg', 'Chapters Box 19/F02/f02.jpg'),
                    source('del', 'copy.jpg', 'Chapters Box 19/Delhivery/copy.jpg'),
                    source('f01', 'f01.jpg', 'Chapters Box 19/F01/f01.jpg'),
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
        self.assertEqual([row['Source Group / Folder'] for row in rows], ['F01', 'F02'])
        self.assertEqual(rows[0]['Prior Open Item'], 'None')
        self.assertEqual(rows[1]['Prior Open Item'], '')
        self.assertEqual([row['Start Filename'] for row in rows], ['f01.jpg', 'f02.jpg'])
        self.assertNotIn('copy.jpg', [row['Start Filename'] for row in rows])
