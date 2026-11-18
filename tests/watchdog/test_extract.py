import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from company_pilot.watchdog import Archive, LocalObjects
from company_pilot.watchdog.archive import SyncError
from company_pilot.watchdog.extract import LIMIT, extract


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Archive(LocalObjects(self.temp.name))

    def test_spreadsheet_multiple_sheets_and_formula_preserved_as_partial_extract(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as file:
            file.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Order A</t></si></sst>')
            file.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1"><f>10+2</f><v>12</v></c></row></sheetData></worksheet>')
            file.writestr("xl/worksheets/sheet2.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>Payment recorded</t></is></c></row></sheetData></worksheet>')
        stream.seek(0)
        record = {"archive": self.archive.put(stream), "mime_type": "application/vnd.google-apps.spreadsheet"}
        result = extract(record, self.archive)
        self.assertIn("A1=Order A", result["text"])
        self.assertIn("fórmula=10+2; valor registrado=12", result["text"])
        self.assertIn("Payment recorded", result["text"])
        self.assertFalse(result["complete"])

    def test_utf8_size_boundary_keeps_valid_partial_text_and_corruption_is_fatal(self):
        data = ("A" + "á" * LIMIT).encode()
        ref = self.archive.put(io.BytesIO(data))
        result = extract({"archive": ref, "mime_type": "text/plain"}, self.archive)
        self.assertTrue(result["text"].startswith("Aáá"))
        self.assertTrue(result["truncated"])
        self.assertFalse(result["complete"])
        part = next((Path(self.temp.name) / "chunks").iterdir())
        part.write_bytes(b"corrupt")
        with self.assertRaises(SyncError):
            extract({"archive": ref, "mime_type": "text/plain"}, self.archive)


if __name__ == "__main__":
    unittest.main()
