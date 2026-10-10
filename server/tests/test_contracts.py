import unittest
from app.models import AssetRow, RecordRow, InspectionOutput

class ContractTests(unittest.TestCase):
    def test_compound_filename_preserved(self):
        row={k:'' for k in ('Project','Job ID','Batch ID','Processing Unit / Source Folder','Asset Sequence','Box','Source Group / Folder','Assigned Record ID','Asset Role','Required Crop Standard','QC Status','Action Needed','Expected Output Asset Count','QC Confidence','QC Note','Odyssey Ingest / Visibility Note','Production Queue Eligible','Production Task Scope','Production Task Note','Exception / Duplicate Note','Inspection Complete','Commit Status','Commit Note')}
        row.update({'Digital Asset':'TriDelta_F03_009_a.jpg','Project':'P','Job ID':'J','Batch ID':'B','Processing Unit / Source Folder':'F03','Assigned Record ID':'R','Inspection Complete':'Yes','Commit Status':'Pending Commit'})
        self.assertEqual(AssetRow.model_validate(row).digital_asset,'TriDelta_F03_009_a.jpg')
    def test_unknown_model_fields_rejected(self):
        with self.assertRaises(Exception): AssetRow.model_validate({'Digital Asset':'x','unexpected':'y'})
    def test_record_kind_is_not_inferred(self):
        row={k:'' for k in ('Project','Job ID','Batch ID','Processing Unit / Source Folder','Record Sequence','Proposed Record ID','Record Kind','Record Operation','Box','Source Group / Folder','Parent Scrapbook','Parent Scrapbook Page','New Batch Assets','Existing Committed Assets','Full Record Assets After Commit','Digital Asset Count','Logical Component Count','Template','Type','Reference Role','Further Detail','Required Crop Standard','Record QC Status','Record Action Needed','Metadata Readiness','Confidence','Review Note','Production Task Count','Odyssey Ingest / Visibility Note','Notes','Boundary State After Record','Inspection Complete','Commit Status','Commit Note')}
        row.update({'Project':'P','Job ID':'J','Batch ID':'B','Processing Unit / Source Folder':'F','Proposed Record ID':'R','Record Kind':'Unknown','Record Operation':'Create New','Inspection Complete':'Yes','Commit Status':'Pending Commit'})
        output=RecordRow.model_validate(row);self.assertEqual(output.record_kind,'Unknown')

if __name__=='__main__':unittest.main()
