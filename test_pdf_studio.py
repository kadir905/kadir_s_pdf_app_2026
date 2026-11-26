import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PDF_STUDIO_TEST_MODE"] = "1"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint

import pdf_studio


class PDFStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = pdf_studio.PDFStudio()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def test_application_builds_main_menus(self):
        menu_names = [action.text() for action in self.window.menuBar().actions()]
        self.assertIn("Google Drive", menu_names)
        self.assertIn("PDF metin dÃ¼zenleme", menu_names)
        self.assertIn("Dosya", menu_names)
        self.assertIn("Çalışma alanı", menu_names)
        self.assertIn("Çalışma paneli", menu_names)
        self.assertIn("Görünüm", menu_names)
        self.assertIn("Yardım", menu_names)
        file_menu = next(action.menu() for action in self.window.menuBar().actions() if action.text() == "Dosya")
        self.assertIn("Yazdır", [action.text() for action in file_menu.actions()])

    def test_google_drive_dialog_is_available_without_credentials(self):
        dialog = pdf_studio.GoogleDriveSyncDialog(self.window)
        self.assertIn("client_secret.json", dialog.credentials_path.text())
        self.assertIsNone(dialog.service)
        dialog.close()

    def test_pdf_text_editor_replaces_word_in_same_bounding_box(self):
        if pdf_studio.fitz is None:
            self.skipTest("PyMuPDF kurulu deÄŸil")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"; output = Path(directory) / "edited.pdf"
            document = pdf_studio.fitz.open(); page = document.new_page(); page.insert_text((40, 50), "Eski metin"); document.save(str(source)); document.close()
            dialog = pdf_studio.PDFTextEditorDialog(self.window, source)
            original_rect = tuple(dialog.words_by_page[0][0][:4])
            dialog.edits = {0: {0: "Yeni"}}
            self.assertTrue(dialog.save_to(output))
            check = pdf_studio.fitz.open(str(output)); extracted = check[0].get_text(); check.close(); dialog.close()
            self.assertIn("Yeni", extracted); self.assertNotIn("Eski", extracted)
            self.assertEqual(tuple(dialog.words_by_page[0][0][:4]), original_rect)

    def test_voice_dialog_has_printer_output_control(self):
        dialog = pdf_studio.VoiceToTextDialog(self.window)
        self.assertTrue(any(button.text() == "Yazdır" for button in dialog.findChildren(pdf_studio.QPushButton)))
        dialog.output.setPlainText("Yazdırılacak sesli metin")
        dialog.close()

    def test_voice_print_without_text_is_blocked(self):
        dialog = pdf_studio.VoiceToTextDialog(self.window)
        dialog.print_transcript()
        self.assertIn("Yazdırmak için önce", dialog.status.text())
        dialog.close()

    def test_voice_transcript_appends_and_keeps_cursor_at_end(self):
        dialog = pdf_studio.VoiceToTextDialog(self.window)
        dialog.append_transcript("ilk cümle")
        dialog.append_transcript("ikinci cümle")
        self.assertEqual(dialog.output.toPlainText(), "ilk cümle ikinci cümle")
        self.assertEqual(dialog.output.textCursor().position(), len("ilk cümle ikinci cümle"))
        dialog.close()

    def test_theme_switching_updates_stylesheet(self):
        self.window.set_theme("light")
        self.assertIn("#f4f6fa", self.window.styleSheet())
        self.window.set_theme("soft")
        self.assertIn("#edf1f5", self.window.styleSheet())
        self.window.set_theme("dark")
        self.assertIn("#101522", self.window.styleSheet())

    def test_panels_can_be_toggled_independently(self):
        self.assertTrue(self.window.side.isVisible())
        self.window.toggle_sidebar()
        self.assertFalse(self.window.side.isVisible())
        self.window.toggle_sidebar()
        self.assertTrue(self.window.side.isVisible())
        self.assertTrue(self.window.right_panel.isVisible())
        self.window.toggle_right_panel()
        self.assertFalse(self.window.right_panel.isVisible())

    def test_dashboard_statistics_are_updated(self):
        self.window.recent = ["one.pdf", "two.pdf"]
        self.window.notes.setPlainText("Kısa not")
        self.window.extracted.setPlainText("bir iki üç dört")
        self.window.update_dashboard()
        self.assertEqual(self.window.stat_cards["files"].text(), "2")
        self.assertEqual(self.window.stat_cards["notes"].text(), "8")
        self.assertEqual(self.window.stat_cards["words"].text(), "4")

    def test_starred_documents_and_local_share(self):
        path = str(Path(tempfile.gettempdir()) / "production-test.pdf")
        self.window.current_file = Path(path)
        self.window.toggle_star_current(); self.assertIn(path, self.window.starred)
        self.window.toggle_star_current(); self.assertNotIn(path, self.window.starred)
        self.window.share_current(); self.assertEqual(QApplication.clipboard().text(), path)

    def test_ai_response_is_html_escaped(self):
        self.window.ai_completed("<script>alert('x')</script>")
        rendered = self.window.ai_chat.toHtml()
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_ai_question_and_error_are_html_escaped(self):
        self.window.ai_prompt.setText("<img src=x onerror=alert(1)>")
        self.window.ai_chat.append(f"<b>Sen:</b> {pdf_studio.escape(self.window.ai_prompt.text())}")
        self.window.ai_failed("<script>alert('error')</script>")
        rendered = self.window.ai_chat.toHtml()
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_language_switching_updates_and_persists(self):
        self.window.set_language("en")
        self.assertEqual(self.window.menuBar().actions()[0].text(), "File")
        self.window.set_language("es")
        self.assertEqual(self.window.menuBar().actions()[0].text(), "Archivo")
        self.window.set_language("tr")
        self.window.save_state()
        state = pdf_studio.json.loads(pdf_studio.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["language"], "tr")

    def test_pdf_notes_export(self):
        if pdf_studio.fitz is None:
            self.skipTest("PyMuPDF kurulu değil")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "notes.pdf"
            self.window.write_notes_pdf(str(output), "Test notu")
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)

    def test_annotation_undo_redo(self):
        self.window.canvas.add_text(QPoint(20, 20), "Deneme")
        self.assertEqual(len(self.window.canvas.annotations), 1)
        self.window.undo_annotation(); self.assertEqual(len(self.window.canvas.annotations), 0)
        self.window.redo_annotation(); self.assertEqual(len(self.window.canvas.annotations), 1)

    def test_loading_a_new_pdf_clears_previous_annotations(self):
        if pdf_studio.fitz is None:
            self.skipTest("PyMuPDF kurulu değil")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "new-document.pdf"
            document = pdf_studio.fitz.open(); document.new_page(); document.save(str(source)); document.close()
            self.window.canvas.add_text(QPoint(20, 20), "Eski belge notu")
            self.assertTrue(self.window.canvas.annotations)
            self.window.load_file(source)
            self.assertEqual(self.window.canvas.annotations, [])
            self.window.doc.close()
            self.window.doc = None

    def test_settings_are_persisted_in_state(self):
        self.window.autosave_enabled = False
        self.window.close_after_export = True
        self.window.save_state()
        self.assertIn('"autosave_enabled": false', pdf_studio.STATE_FILE.read_text(encoding="utf-8"))
        self.assertIn('"close_after_export": true', pdf_studio.STATE_FILE.read_text(encoding="utf-8"))

    def test_pdf_search_finds_matching_text(self):
        if pdf_studio.fitz is None:
            self.skipTest("PyMuPDF kurulu değil")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "search.pdf"; document = pdf_studio.fitz.open(); page = document.new_page(); page.insert_text((40, 50), "Kadir PDF test metni"); document.save(str(source)); document.close()
            self.window.load_file(source); self.window.search.setText("test metni"); self.window.search_document()
            self.assertEqual(self.window.page_index, 0); self.assertIn("sonuç bulundu", self.window.statusBar().currentMessage())
            self.window.doc.close(); self.window.doc = None

    def test_annotations_are_written_to_exported_pdf(self):
        if pdf_studio.fitz is None:
            self.skipTest("PyMuPDF kurulu değil")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"; output = Path(directory) / "export.pdf"; document = pdf_studio.fitz.open(); document.new_page(); document.save(str(source)); document.close()
            self.window.load_file(source); page_rect = self.window.canvas.page_rect(); self.window.canvas.add_text(page_rect.topLeft().toPoint() + QPoint(20, 20), "Kaydedildi", {"size": 12, "color": "#1467d8"}); self.window.apply_annotations(self.window.doc)
            self.window.doc.save(str(output)); self.window.doc.close(); self.window.doc = None
            check = pdf_studio.fitz.open(str(output)); extracted = check[0].get_text(); check.close(); self.assertIn("Kaydedildi", extracted)

    def test_text_render_limit_is_applied_when_loading_pdf(self):
        if pdf_studio.fitz is None:
            self.skipTest("PyMuPDF kurulu değil")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "large-text.pdf"
            document = pdf_studio.fitz.open()
            page = document.new_page()
            page.insert_text((40, 50), "x " * 5000)
            document.save(str(source)); document.close()
            self.window.load_file(source)
            self.assertLessEqual(len(self.window.extracted.toPlainText()), 200100)
            self.window.doc.close()
            self.window.doc = None

    def test_pdf_page_is_rendered_to_rgb_pixmap(self):
        if pdf_studio.fitz is None:
            self.skipTest("PyMuPDF kurulu değil")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rendered-text.pdf"
            document = pdf_studio.fitz.open(); page = document.new_page()
            page.insert_text((40, 50), "Görüntülenecek metin", color=(0, 0, 0))
            document.save(str(source)); document.close()
            self.window.load_file(source)
            self.assertTrue(self.window.canvas.pixmaps)
            self.assertFalse(self.window.canvas.pixmaps[0].isNull())
            self.assertGreater(self.window.canvas.pixmaps[0].width(), 0)
            self.window.doc.close(); self.window.doc = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
