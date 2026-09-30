import unittest
from commit_engine import plan_commit, CommitError


class F05AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.queue = {
            "Batch ID": "TDC-B19-F05-B01",
            "Batch Status": "Inspection Complete - Awaiting Commit",
            "Planned Asset Count": "10",
        }
        self.record_rows = [
            {
                "Project": "Tri Delta Chapters", "Batch ID": "TDC-B19-F05-B01",
                "Processing Unit / Source Folder": "F05 Technical Batch 01",
                "Proposed Record ID": "TDC-B19-F05-REF-001", "Record Kind": "Metadata Only",
                "Record Operation": "Create New", "Box": "Chapters Box 19", "Source Group / Folder": "F05",
                "New Batch Assets": "F05_001.jpg", "Full Record Assets After Commit": "F05_001.jpg",
                "Digital Asset Count": "1", "Logical Component Count": "0", "Template": "Metadata Only",
                "Reference Role": "Folder cover/context", "Further Detail": "Blank folder/enclosure capture.",
                "Required Crop Standard": "N/A", "Record QC Status": "Reference Only", "Record Action Needed": "N/A",
                "Metadata Readiness": "Metadata Reference", "Confidence": "High", "Production Task Count": "0",
                "Odyssey Ingest / Visibility Note": "Exclude from normal Odyssey ingest.",
                "Notes": "Context only", "Boundary State After Record": "Closed",
                "Inspection Complete": "Yes", "Commit Status": "Pending Commit",
            },
            {
                "Project": "Tri Delta Chapters", "Batch ID": "TDC-B19-F05-B01",
                "Processing Unit / Source Folder": "F05 Technical Batch 01",
                "Proposed Record ID": "TDC-B19-F05-001", "Record Kind": "Normal Odyssey",
                "Record Operation": "Create New", "Box": "Chapters Box 19", "Source Group / Folder": "F05",
                "New Batch Assets": "; ".join(f"F05_{i:03d}.jpg" for i in range(2,11)),
                "Full Record Assets After Commit": "; ".join(f"F05_{i:03d}.jpg" for i in range(2,11)),
                "Digital Asset Count": "9", "Logical Component Count": "9", "Template": "Document",
                "Type": "Biographical Notes", "Further Detail": "Bound handwritten notes notebook.",
                "Required Crop Standard": "Book Page Edge", "Record QC Status": "Action Required",
                "Record Action Needed": "Crop", "Metadata Readiness": "Blocked - Cropping/QC", "Confidence": "High",
                "Production Task Count": "9", "Odyssey Ingest / Visibility Note": "Retain in sequence.",
                "Notes": "Notebook continues beyond the batch.",
                "Boundary State After Record": "Open - continues after F05_010.jpg",
                "Inspection Complete": "Yes", "Commit Status": "Pending Commit",
            }
        ]
        self.asset_rows = []
        self.asset_rows.append({
            "Project":"Tri Delta Chapters","Batch ID":"TDC-B19-F05-B01",
            "Processing Unit / Source Folder":"F05 Technical Batch 01","Digital Asset":"F05_001.jpg",
            "Box":"Chapters Box 19","Source Group / Folder":"F05","Assigned Record ID":"TDC-B19-F05-REF-001",
            "Asset Role":"Folder cover/context","Required Crop Standard":"N/A","QC Status":"Reference Only",
            "Action Needed":"N/A","Expected Output Asset Count":"1","QC Confidence":"High","QC Note":"Context",
            "Odyssey Ingest / Visibility Note":"Exclude","Production Queue Eligible":"No",
            "Inspection Complete":"Yes","Commit Status":"Pending Commit"
        })
        for i in range(2,11):
            self.asset_rows.append({
                "Project":"Tri Delta Chapters","Batch ID":"TDC-B19-F05-B01",
                "Processing Unit / Source Folder":"F05 Technical Batch 01","Digital Asset":f"F05_{i:03d}.jpg",
                "Box":"Chapters Box 19","Source Group / Folder":"F05","Assigned Record ID":"TDC-B19-F05-001",
                "Asset Role":"Notebook page","Required Crop Standard":"Book Page Edge","QC Status":"Action Required",
                "Action Needed":"Crop","Expected Output Asset Count":"1","QC Confidence":"High","QC Note":"Crop needed",
                "Odyssey Ingest / Visibility Note":"Retain in sequence","Production Queue Eligible":"Yes",
                "Production Task Scope":"Single notebook page","Production Task Note":"Crop to page edge",
                "Inspection Complete":"Yes","Commit Status":"Pending Commit"
            })
        self.existing = {
            "Asset QC": [], "Itemization": [], "Metadata References": [], "Ready for Metadata": [],
            "Production Queue": [
                {"Task ID": f"TDC-B19-F02-TASK-{i:03d}", "Task Status":"Open", "Action Needed":"Crop"}
                for i in range(1,7)
            ] + [
                {"Task ID":"TDC-B19-F03-TASK-001","Task Status":"Open","Action Needed":"Split Spread"},
                {"Task ID":"TDC-B19-F04-TASK-001","Task Status":"Open","Action Needed":"Crop"},
            ]
        }

    def test_f05_plan(self):
        plan = plan_commit(self.queue, self.asset_rows, self.record_rows, self.existing, "09/23/26")
        self.assertEqual(len(plan.append_rows["Asset QC"]), 10)
        self.assertEqual(len(plan.append_rows["Itemization"]), 1)
        self.assertEqual(len(plan.append_rows["Metadata References"]), 1)
        self.assertEqual(len(plan.append_rows["Production Queue"]), 9)
        self.assertEqual(len(plan.append_rows.get("Ready for Metadata", [])), 0)
        self.assertEqual(plan.resulting_open_item, "TDC-B19-F05-001")
        self.assertEqual(plan.new_task_count, 9)
        self.assertEqual(plan.new_normal_record_count, 1)
        self.assertEqual(plan.new_reference_count, 1)
        self.assertEqual(plan.blocked_record_count, 1)
        self.assertEqual(plan.append_rows["Production Queue"][0]["Task ID"], "TDC-B19-F05-TASK-001")
        self.assertEqual(plan.append_rows["Production Queue"][-1]["Task ID"], "TDC-B19-F05-TASK-009")

    def test_idempotent_noop(self):
        q = dict(self.queue)
        q["Batch Status"] = "Committed - Validated"
        plan = plan_commit(q, self.asset_rows, self.record_rows, self.existing, "09/23/26")
        self.assertTrue(plan.idempotent_noop)

    def test_duplicate_asset_blocks_commit(self):
        existing = dict(self.existing)
        existing["Asset QC"] = [{"Digital Asset":"F05_004.jpg"}]
        with self.assertRaises(CommitError):
            plan_commit(self.queue, self.asset_rows, self.record_rows, existing, "09/23/26")


if __name__ == "__main__":
    unittest.main()
