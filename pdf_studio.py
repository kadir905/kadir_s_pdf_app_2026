import json
from html import escape
import math
import os
import queue
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import textwrap
from datetime import datetime
import urllib.error
import urllib.request
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None
try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

from PySide6.QtCore import QPoint, QRectF, QDate, QTime, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout,
    QCheckBox, QDialog, QDialogButtonBox, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QStatusBar, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QMenuBar,
    QComboBox, QColorDialog, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCalendarWidget, QTimeEdit)

APP_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", APP_DIR / "user_data")) / "KadirsPDFApp"
try:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    USER_DATA_DIR = APP_DIR / "user_data"
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = USER_DATA_DIR / "pdf_studio_state.json"
VOSK_MODEL_NAME = "vosk-model-small-tr-0.3"


def bundled_vosk_model_path():
    """Find the bundled Turkish model in the app root or models subfolder."""
    candidates = (APP_DIR / VOSK_MODEL_NAME, APP_DIR / "models" / VOSK_MODEL_NAME)
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "final.mdl").exists():
            return str(candidate)
    return str(candidates[0])


class OllamaWorker(QThread):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, model, prompt, parent=None):
        super().__init__(parent); self.model = model; self.prompt = prompt

    def run(self):
        try:
            # Streaming keeps the socket active while Ollama loads the model or
            # generates a long answer. A single response can time out meanwhile.
            payload = json.dumps({
                "model": self.model,
                "prompt": self.prompt,
                "stream": True,
                "keep_alive": "10m",
                "options": {"temperature": 0.2, "num_predict": 700},
            }, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            parts = []
            with urllib.request.urlopen(request, timeout=300) as response:
                for raw_line in response:
                    if self.isInterruptionRequested():
                        return
                    if not raw_line.strip():
                        continue
                    chunk = json.loads(raw_line.decode("utf-8"))
                    if chunk.get("error"):
                        raise RuntimeError(chunk["error"])
                    parts.append(chunk.get("response", ""))
                    if chunk.get("done"):
                        break
            self.completed.emit("".join(parts).strip() or "Yanıt alınamadı.")
        except TimeoutError:
            self.failed.emit("AI isteği 5 dakika içinde tamamlanmadı. Daha kısa bir PDF bölümüyle tekrar deneyin.")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
            except Exception:
                detail = str(exc)
            self.failed.emit(f"Ollama isteği reddetti: {detail}")
        except urllib.error.URLError:
            self.failed.emit("Ollama'ya bağlanılamadı. Ollama'nın çalıştığından ve Qwen modelinin kurulu olduğundan emin olun.")
        except Exception as exc:
            self.failed.emit(f"AI isteği başarısız: {exc}")


class OllamaSetupWorker(QThread):
    status = Signal(str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent); self.active_process = None

    def run(self):
        try:
            ollama_bin = shutil.which("ollama")
            if not ollama_bin:
                self.failed.emit("Ollama bulunamadı. Ollama'yı kurduktan sonra uygulamayı yeniden başlatın."); return
            self.status.emit("Ollama kontrol ediliyor…")
            check = subprocess.run([ollama_bin, "list"], capture_output=True, text=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ollama_ready = check.returncode == 0
            if not ollama_ready:
                self.status.emit("Ollama servisi başlatılıyor…")
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self.active_process = subprocess.Popen([ollama_bin, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                import time; time.sleep(3)
                check = subprocess.run([ollama_bin, "list"], capture_output=True, text=True, timeout=8, creationflags=flags)
                if check.returncode != 0: self.failed.emit("Ollama servisi başlatılamadı."); return
            if "qwen2.5:7b" not in check.stdout:
                self.status.emit("Qwen 2.5 modeli indiriliyor…")
                self.active_process = subprocess.Popen([ollama_bin, "pull", "qwen2.5:7b"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                while self.active_process.poll() is None:
                    if self.isInterruptionRequested(): self.active_process.terminate(); self.failed.emit("Qwen model kurulumu kullanıcı tarafından durduruldu."); return
                    import time; time.sleep(0.25)
                if self.active_process.returncode != 0: self.failed.emit("Qwen modeli indirilemedi. Ollama terminal çıktısını kontrol edin."); return
            self.completed.emit("Ollama hazır · Qwen 2.5 kullanılabilir")
        except subprocess.TimeoutExpired:
            self.failed.emit("Ollama işlemi zaman aşımına uğradı.")
        except Exception as exc:
            self.failed.emit(f"Ollama başlatılamadı: {exc}")

    def stop(self):
        self.requestInterruption()
        if self.active_process and self.active_process.poll() is None: self.active_process.terminate()


class SpeechWorker(QThread):
    completed = Signal()
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, text, parent=None):
        super().__init__(parent); self.text = text; self.engine = None

    def run(self):
        try:
            import pyttsx3
            self.engine = pyttsx3.init(); voices = self.engine.getProperty("voices"); voice = next((v for v in voices if "tolga" in (getattr(v, "name", "") + " " + getattr(v, "id", "")).lower()), None)
            if voice:
                self.engine.setProperty("voice", voice.id); self.status.emit("Tolga sesi kullanılıyor")
            else:
                self.status.emit("Tolga sesi bulunamadı; varsayılan Windows sesi kullanılıyor")
            self.engine.say(self.text); self.engine.runAndWait(); self.completed.emit()
        except Exception as exc:
            self.failed.emit(f"Seslendirme başlatılamadı: {exc}")

    def stop(self):
        if self.engine: self.engine.stop()


class VoskWorker(QThread):
    transcript = Signal(str)
    partial = Signal(str)
    started = Signal()
    finished = Signal()
    failed = Signal(str)

    def __init__(self, model_path, parent=None):
        super().__init__(parent); self.model_path = Path(model_path); self.stop_event = threading.Event(); self.audio_queue = queue.Queue()

    def run(self):
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model
            if not self.model_path.is_dir():
                self.failed.emit("Vosk modeli bulunamadı. Önce Türkçe Vosk model klasörünü seçin."); return
            model = Model(str(self.model_path)); recognizer = KaldiRecognizer(model, 16000)

            def audio_callback(indata, frames, time_info, status):
                if status: self.partial.emit(f"Ses aygıtı: {status}")
                self.audio_queue.put(bytes(indata))

            with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16", channels=1, callback=audio_callback):
                self.started.emit()
                while not self.stop_event.is_set():
                    try: data = self.audio_queue.get(timeout=0.2)
                    except queue.Empty: continue
                    if recognizer.AcceptWaveform(data):
                        text = json.loads(recognizer.Result()).get("text", "").strip()
                        if text: self.transcript.emit(text)
                    else:
                        partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
                        if partial: self.partial.emit(partial)
                final_text = json.loads(recognizer.FinalResult()).get("text", "").strip()
                if final_text: self.transcript.emit(final_text)
            self.finished.emit()
        except ImportError as exc:
            self.failed.emit(f"Sesli metin için gerekli paket eksik: {exc}. requirements.txt dosyasını kurun.")
        except Exception as exc:
            self.failed.emit(f"Mikrofon/Vosk başlatılamadı: {exc}")

    def stop(self):
        self.stop_event.set()


class VoiceToTextDialog(QDialog):
    def __init__(self, owner, parent=None):
        super().__init__(parent or owner); self.owner = owner; self.worker = None
        self.setWindowTitle("Vosk · Sesli metin")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        intro = QLabel("Mikrofonunuzdan gelen Türkçe sesi yerel Vosk modeliyle metne dönüştürün. Ses bilgisayardan dışarı gönderilmez."); intro.setWordWrap(True); intro.setObjectName("muted"); layout.addWidget(intro)
        saved_model_path = getattr(owner, "vosk_model_path", "")
        if not Path(saved_model_path).is_dir(): saved_model_path = bundled_vosk_model_path()
        model_row = QHBoxLayout(); model_row.addWidget(QLabel("Vosk model klasörü:")); self.model_path = QLineEdit(saved_model_path); model_row.addWidget(self.model_path, 1); browse = QPushButton("Gözat"); browse.clicked.connect(self.choose_model); model_row.addWidget(browse); layout.addLayout(model_row)
        self.status = QLabel("Hazır · Kayıt başlatmak için düğmeye basın", objectName="muted"); layout.addWidget(self.status)
        self.partial_label = QLabel("Dinlenen bölüm: —", objectName="muted"); self.partial_label.setWordWrap(True); layout.addWidget(self.partial_label)
        layout.addWidget(QLabel("Dönüştürülen metin:")); self.output = QTextEdit(); self.output.setPlaceholderText("Konuştuğunuz metin burada görünecek…"); layout.addWidget(self.output, 1)
        controls = QHBoxLayout(); self.start_button = QPushButton("●  Dinlemeyi başlat"); self.start_button.setObjectName("primary"); self.start_button.clicked.connect(self.start_listening); controls.addWidget(self.start_button); self.stop_button = QPushButton("Durdur"); self.stop_button.setEnabled(False); self.stop_button.clicked.connect(self.stop_listening); controls.addWidget(self.stop_button); self.speak_button = QPushButton("🔊 Tolga ile seslendir"); self.speak_button.clicked.connect(self.speak_transcript); controls.addWidget(self.speak_button); pdf_button = QPushButton("PDF olarak kaydet"); pdf_button.clicked.connect(self.save_transcript_pdf); controls.addWidget(pdf_button); print_button = QPushButton("Yazdır"); print_button.setToolTip("Sesli metni seçtiğiniz yazıcıdan yazdır"); print_button.clicked.connect(self.print_transcript); controls.addWidget(print_button); controls.addStretch(); copy_button = QPushButton("Panoya kopyala"); copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.output.toPlainText())); controls.addWidget(copy_button); ai_button = QPushButton("AI sorusuna aktar"); ai_button.clicked.connect(self.send_to_ai); controls.addWidget(ai_button); notes_button = QPushButton("Notlara aktar"); notes_button.clicked.connect(self.send_to_notes); controls.addWidget(notes_button); layout.addLayout(controls)

    def choose_model(self):
        path = QFileDialog.getExistingDirectory(self, "Vosk model klasörünü seç", self.model_path.text() or str(APP_DIR))
        if path: self.model_path.setText(path)

    def start_listening(self):
        if self.worker and self.worker.isRunning(): return
        self.owner.vosk_model_path = self.model_path.text().strip(); self.owner.save_state(); self.partial_label.setText("Dinlenen bölüm: …"); self.status.setText("Mikrofon açılıyor…")
        self.worker = VoskWorker(self.owner.vosk_model_path, self); self.worker.started.connect(lambda: self.status.setText("Dinleniyor · konuşabilirsiniz")); self.worker.transcript.connect(self.append_transcript); self.worker.partial.connect(lambda text: self.partial_label.setText(f"Dinlenen bölüm: {text}")); self.worker.failed.connect(self.voice_failed); self.worker.finished.connect(self.voice_finished); self.worker.start(); self.start_button.setEnabled(False); self.stop_button.setEnabled(True)

    def append_transcript(self, text):
        current = self.output.toPlainText().strip(); self.output.setPlainText(f"{current} {text}".strip()); cursor = self.output.textCursor(); cursor.movePosition(cursor.End); self.output.setTextCursor(cursor)

    def stop_listening(self):
        if self.worker and self.worker.isRunning(): self.status.setText("Kayıt durduruluyor…"); self.worker.stop(); self.worker.wait(2500)
        self.start_button.setEnabled(True); self.stop_button.setEnabled(False)

    def voice_finished(self):
        self.start_button.setEnabled(True); self.stop_button.setEnabled(False); self.status.setText("Dinleme tamamlandı · Tolga sesiyle okunuyor…")
        if self.output.toPlainText().strip(): self.speak_transcript()

    def voice_failed(self, message):
        self.start_button.setEnabled(True); self.stop_button.setEnabled(False); self.status.setText(message); QMessageBox.warning(self, "Vosk sesli metin", message)

    def speak_transcript(self):
        text = self.output.toPlainText().strip()
        if not text: self.status.setText("Önce sesinizi metne dönüştürün"); return
        if not pyttsx3: self.status.setText("Seslendirme için pyttsx3 kurun: pip install pyttsx3"); return
        if hasattr(self, "speech_worker") and self.speech_worker.isRunning(): self.speech_worker.stop(); self.speech_worker.wait(500)
        self.speech_worker = SpeechWorker(text, self); self.speech_worker.status.connect(self.status.setText); self.speech_worker.completed.connect(lambda: self.status.setText("Metin Tolga sesiyle okundu")); self.speech_worker.failed.connect(self.voice_failed); self.speech_worker.start()

    def save_transcript_pdf(self):
        text = self.output.toPlainText().strip()
        if not text: self.status.setText("PDF kaydetmek için önce metin oluşturun"); return
        if not fitz: QMessageBox.warning(self, "PDF kaydetme", "PDF oluşturmak için PyMuPDF kurun: pip install PyMuPDF"); return
        path, _ = QFileDialog.getSaveFileName(self, "Ses metnini PDF olarak kaydet", "sesli_metin.pdf", "PDF dosyaları (*.pdf)")
        if not path: return
        try:
            self.owner.write_notes_pdf(path, text); self.status.setText("Ses metni PDF olarak kaydedildi")
        except Exception as exc: QMessageBox.critical(self, "PDF kaydetme", str(exc))

    def print_transcript(self):
        """Open the system printer dialog and print the current voice transcript."""
        text = self.output.toPlainText().strip()
        if not text:
            self.status.setText("Yazdırmak için önce sesinizi metne dönüştürün")
            return
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Sesli metni yazdır")
        if dialog.exec() != QDialog.Accepted:
            self.status.setText("Yazdırma iptal edildi")
            return
        document = QTextDocument()
        document.setPlainText(text)
        document.print_(printer)
        self.status.setText("Sesli metin yazıcıya gönderildi")

    def send_to_ai(self):
        text = self.output.toPlainText().strip()
        if text: self.owner.ai_prompt.setText(text); self.owner.tabs.setCurrentIndex(6); self.accept()

    def send_to_notes(self):
        text = self.output.toPlainText().strip()
        if text: self.owner.notes.insertPlainText(("\n" if self.owner.notes.toPlainText().strip() else "") + text); self.accept()

    def closeEvent(self, event):
        self.stop_listening()
        if hasattr(self, "speech_worker") and self.speech_worker.isRunning(): self.speech_worker.stop(); self.speech_worker.wait(1000)
        super().closeEvent(event)


class QwenFineTuningDialog(QDialog):
    """Prepare Turkish instruction data for Qwen without pretending to train in Ollama."""

    def __init__(self, owner, parent=None):
        super().__init__(parent or owner)
        self.owner = owner
        self.examples = [dict(example) for example in getattr(owner, "qwen_finetune_examples", [])]
        self.setWindowTitle("Qwen 2.5 · Türkçe fine-tuning hazırlığı")
        self.resize(980, 720)
        layout = QVBoxLayout(self)
        intro = QLabel("Türkçe örnekler ekleyerek Qwen için kaliteli eğitim verisi hazırlayın. Ollama modeli doğrudan eğitmez; bu ekran JSONL veri seti ve Ollama Modelfile üretir.")
        intro.setWordWrap(True); intro.setObjectName("muted"); layout.addWidget(intro)

        model_row = QHBoxLayout(); model_row.addWidget(QLabel("Temel model:")); self.base_model = QLineEdit(getattr(owner, "ai_model", None).text() if hasattr(getattr(owner, "ai_model", None), "text") else "qwen2.5:7b"); model_row.addWidget(self.base_model); layout.addLayout(model_row)
        layout.addWidget(QLabel("Sistem talimatı (modelin Türkçe cevap davranışı):"))
        self.system_prompt = QTextEdit(getattr(owner, "qwen_system_prompt", "")); self.system_prompt.setMaximumHeight(90); layout.addWidget(self.system_prompt)

        form = QHBoxLayout()
        left = QVBoxLayout(); left.addWidget(QLabel("Kullanıcı / PDF bağlamı")); self.user_text = QTextEdit(); self.user_text.setPlaceholderText("Örnek soru veya PDF bağlamı…"); left.addWidget(self.user_text)
        right = QVBoxLayout(); right.addWidget(QLabel("Beklenen Türkçe cevap")); self.assistant_text = QTextEdit(); self.assistant_text.setPlaceholderText("Doğru, mantıklı ve kısa örnek cevap…"); right.addWidget(self.assistant_text)
        form.addLayout(left, 1); form.addLayout(right, 1); layout.addLayout(form)
        add_row = QHBoxLayout(); add_button = QPushButton("Örneği ekle / güncelle"); add_button.clicked.connect(self.add_example); add_row.addWidget(add_button); remove_button = QPushButton("Seçili örneği sil"); remove_button.clicked.connect(self.remove_selected); add_row.addWidget(remove_button); add_row.addStretch(); layout.addLayout(add_row)

        self.table = QTableWidget(0, 3); self.table.setHorizontalHeaderLabels(["Kullanıcı / bağlam", "Beklenen cevap", "Durum"]); self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.itemSelectionChanged.connect(self.load_selected); layout.addWidget(self.table, 1)
        self.refresh_table()

        buttons = QHBoxLayout(); import_button = QPushButton("JSONL içe aktar"); import_button.clicked.connect(self.import_jsonl); buttons.addWidget(import_button); export_button = QPushButton("JSONL dışa aktar"); export_button.clicked.connect(self.export_jsonl); buttons.addWidget(export_button); modelfile_button = QPushButton("Modelfile oluştur"); modelfile_button.clicked.connect(self.export_modelfile); buttons.addWidget(modelfile_button); buttons.addStretch(); close_button = QPushButton("Kaydet ve kapat"); close_button.setObjectName("primary"); close_button.clicked.connect(self.save_and_close); buttons.addWidget(close_button); layout.addLayout(buttons)

    def refresh_table(self):
        self.table.setRowCount(0)
        for example in self.examples:
            row = self.table.rowCount(); self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(example.get("user", "")))
            self.table.setItem(row, 1, QTableWidgetItem(example.get("assistant", "")))
            self.table.setItem(row, 2, QTableWidgetItem("Hazır" if example.get("user") and example.get("assistant") else "Eksik"))

    def add_example(self):
        user = self.user_text.toPlainText().strip(); assistant = self.assistant_text.toPlainText().strip()
        if not user or not assistant:
            QMessageBox.warning(self, "Eksik örnek", "Kullanıcı/bağlam ve beklenen cevap alanlarını doldurun."); return
        row = self.table.currentRow(); example = {"user": user, "assistant": assistant}
        if 0 <= row < len(self.examples): self.examples[row] = example
        else: self.examples.append(example)
        self.refresh_table(); self.user_text.clear(); self.assistant_text.clear()

    def load_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.examples):
            self.user_text.setPlainText(self.examples[row].get("user", "")); self.assistant_text.setPlainText(self.examples[row].get("assistant", ""))

    def remove_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.examples): self.examples.pop(row); self.refresh_table()

    def import_jsonl(self):
        path, _ = QFileDialog.getOpenFileName(self, "JSONL veri seti aç", "", "JSONL (*.jsonl);;JSON (*.json)")
        if not path: return
        added = 0
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                record = json.loads(line); messages = record.get("messages", []); user = next((m.get("content", "") for m in messages if m.get("role") == "user"), ""); assistant = next((m.get("content", "") for m in messages if m.get("role") == "assistant"), "")
                if user and assistant: self.examples.append({"user": user, "assistant": assistant}); added += 1
            self.refresh_table(); self.owner.status_msg(f"{added} fine-tuning örneği içe aktarıldı")
        except Exception as exc: QMessageBox.critical(self, "JSONL içe aktarma", f"Dosya okunamadı: {exc}")

    def dataset_records(self):
        system = self.system_prompt.toPlainText().strip()
        records = []
        for example in self.examples:
            messages = []
            if system: messages.append({"role": "system", "content": system})
            messages.extend([{"role": "user", "content": example["user"]}, {"role": "assistant", "content": example["assistant"]}])
            records.append({"messages": messages})
        return records

    def export_jsonl(self):
        path, _ = QFileDialog.getSaveFileName(self, "JSONL veri setini kaydet", "qwen_turkish_dataset.jsonl", "JSONL (*.jsonl)")
        if not path: return
        try:
            Path(path).write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in self.dataset_records()) + "\n", encoding="utf-8"); self.owner.status_msg("Qwen JSONL veri seti kaydedildi")
        except Exception as exc: QMessageBox.critical(self, "JSONL dışa aktarma", f"Dosya kaydedilemedi: {exc}")

    def export_modelfile(self):
        path, _ = QFileDialog.getSaveFileName(self, "Ollama Modelfile kaydet", "Modelfile.qwen-tr", "Modelfile (*)")
        if not path: return
        system = self.system_prompt.toPlainText().strip() or "Türkçe cevap veren, PDF bağlamına sadık, mantıklı ve anlaşılır bir belge asistanısın."
        def modelfile_text(value): return " ".join(value.splitlines()).replace("\\", "\\\\").replace('"', '\\"')
        lines = [f"FROM {self.base_model.text().strip() or 'qwen2.5:7b'}", f"SYSTEM {modelfile_text(system)}", "PARAMETER temperature 0.2", "PARAMETER num_predict 700"]
        for example in self.examples:
            lines.extend([f"MESSAGE user {modelfile_text(example['user'])}", f"MESSAGE assistant {modelfile_text(example['assistant'])}"])
        try: Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8"); self.owner.status_msg("Ollama Modelfile kaydedildi")
        except Exception as exc: QMessageBox.critical(self, "Modelfile dışa aktarma", f"Dosya kaydedilemedi: {exc}")

    def save_and_close(self):
        self.owner.qwen_finetune_examples = self.examples; self.owner.qwen_system_prompt = self.system_prompt.toPlainText().strip(); self.owner.save_state(); self.accept()


class ReminderStore:
    def __init__(self):
        self.path = USER_DATA_DIR / "calendar_reminders.sqlite3"
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reminder_date TEXT NOT NULL,
                reminder_time TEXT NOT NULL,
                note TEXT NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""")
            connection.commit()

    def connect(self):
        return sqlite3.connect(str(self.path))

    def add(self, reminder_date, reminder_time, note):
        with self.connect() as connection:
            connection.execute("INSERT INTO reminders (reminder_date, reminder_time, note, created_at) VALUES (?, ?, ?, ?)", (reminder_date, reminder_time, note, datetime.now().isoformat(timespec="seconds")))
            connection.commit()

    def list_all(self):
        with self.connect() as connection:
            return connection.execute("SELECT id, reminder_date, reminder_time, note, notified FROM reminders ORDER BY reminder_date, reminder_time, id").fetchall()

    def delete(self, reminder_id):
        with self.connect() as connection:
            connection.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)); connection.commit()

    def due(self, now):
        due_key = now.strftime("%Y-%m-%d %H:%M")
        with self.connect() as connection:
            return connection.execute("SELECT id, reminder_date, reminder_time, note FROM reminders WHERE notified = 0 AND reminder_date || ' ' || reminder_time <= ? ORDER BY reminder_date, reminder_time", (due_key,)).fetchall()

    def mark_notified(self, reminder_ids):
        if not reminder_ids: return
        with self.connect() as connection:
            connection.executemany("UPDATE reminders SET notified = 1 WHERE id = ?", [(reminder_id,) for reminder_id in reminder_ids]); connection.commit()


class DoubleClickCalendar(QCalendarWidget):
    date_double_clicked = Signal(QDate)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        QTimer.singleShot(0, lambda: self.date_double_clicked.emit(self.selectedDate()))


class CalendarDialog(QDialog):
    def __init__(self, owner, parent=None):
        super().__init__(parent or owner); self.owner = owner; self.store = owner.reminder_store
        self.setWindowTitle("Takvim ve hatırlatıcılar"); self.resize(760, 650)
        layout = QVBoxLayout(self)
        intro = QLabel("Bir güne çift tıklayarak tarih, saat ve not ekleyin. Vadesi gelen notlar uygulama açıkken otomatik olarak uyarı olarak gösterilir."); intro.setWordWrap(True); intro.setObjectName("muted"); layout.addWidget(intro)
        self.calendar = DoubleClickCalendar(); self.calendar.setGridVisible(True); self.calendar.setMinimumHeight(280); self.calendar.date_double_clicked.connect(self.open_note_editor); layout.addWidget(self.calendar)
        layout.addWidget(QLabel("Kayıtlı hatırlatıcılar:"))
        self.table = QTableWidget(0, 3); self.table.setHorizontalHeaderLabels(["Tarih", "Saat", "Not"]); self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); layout.addWidget(self.table, 1)
        buttons = QHBoxLayout(); add_button = QPushButton("Seçili güne not ekle"); add_button.clicked.connect(lambda: self.open_note_editor(self.calendar.selectedDate())); buttons.addWidget(add_button); delete_button = QPushButton("Seçili notu sil"); delete_button.clicked.connect(self.delete_selected); buttons.addWidget(delete_button); buttons.addStretch(); close_button = QPushButton("Kapat"); close_button.clicked.connect(self.accept); buttons.addWidget(close_button); layout.addLayout(buttons)
        self.refresh()

    def open_note_editor(self, selected_date):
        dialog = QDialog(self); dialog.setWindowTitle(f"{selected_date.toString('dd.MM.yyyy')} için hatırlatıcı"); dialog.resize(480, 300); form = QVBoxLayout(dialog)
        form.addWidget(QLabel(f"Tarih: {selected_date.toString('dd.MM.yyyy')}")); form.addWidget(QLabel("Saat:")); time_edit = QTimeEdit(QTime.currentTime()); time_edit.setDisplayFormat("HH:mm"); form.addWidget(time_edit); form.addWidget(QLabel("Not:")); note_edit = QTextEdit(); note_edit.setPlaceholderText("Bu tarihte gösterilecek not…"); form.addWidget(note_edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Save).setText("Kaydet"); buttons.button(QDialogButtonBox.Cancel).setText("İptal"); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            note = note_edit.toPlainText().strip()
            if not note: QMessageBox.warning(self, "Eksik not", "Hatırlatıcı notunu yazın."); return
            self.store.add(selected_date.toString("yyyy-MM-dd"), time_edit.time().toString("HH:mm"), note); self.refresh(); self.owner.status_msg("Takvim notu kaydedildi")

    def refresh(self):
        self.table.setRowCount(0)
        for reminder_id, reminder_date, reminder_time, note, notified in self.store.list_all():
            row = self.table.rowCount(); self.table.insertRow(row); date_item = QTableWidgetItem(reminder_date); date_item.setData(Qt.UserRole, reminder_id); self.table.setItem(row, 0, date_item); self.table.setItem(row, 1, QTableWidgetItem(reminder_time)); self.table.setItem(row, 2, QTableWidgetItem(("[Bildirim gösterildi] " if notified else "") + note))

    def delete_selected(self):
        row = self.table.currentRow()
        if row < 0: return
        reminder_id = self.table.item(row, 0).data(Qt.UserRole); self.store.delete(reminder_id); self.refresh(); self.owner.status_msg("Takvim notu silindi")


class AppLogo(QWidget):
    """Lightweight vector logo so the app has no external image dependency."""

    def __init__(self, compact=False):
        super().__init__(); self.compact = compact; self.setFixedSize(42 if compact else 180, 42 if compact else 76)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(3, 3, 36, 36); painter.setPen(QPen(QColor("#6da9ff"), 2)); painter.setBrush(QColor("#172d4d")); painter.drawRoundedRect(box, 9, 9)
        painter.setPen(QPen(QColor("#91c1ff"), 2)); painter.setBrush(Qt.NoBrush); painter.drawLine(12, 12, 12, 30); painter.drawLine(12, 12, 27, 12); painter.drawLine(12, 21, 24, 21); painter.drawLine(12, 30, 27, 30)
        painter.setBrush(QColor("#a882ff")); painter.setPen(Qt.NoPen); painter.drawEllipse(QRectF(25, 7, 9, 9))
        if not self.compact:
            painter.setPen(QColor("#eef3f9")); painter.setFont(QFont("Segoe UI", 13, QFont.Bold)); painter.drawText(48, 26, "Kadir's PDF App"); painter.setPen(QColor("#8f9aaa")); painter.setFont(QFont("Segoe UI", 8)); painter.drawText(49, 42, "PDF DOCUMENT WORKSPACE")


class PageCanvas(QWidget):
    annotation_added = Signal(str)
    text_clicked = Signal(QPoint)

    def __init__(self):
        super().__init__(); self.setMinimumSize(700, 650); self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.pixmap = QPixmap(); self.pixmaps = []; self.page_rects = []; self.page_spacing = 28
        self.current_page = 0; self.tool = "select"; self.annotations = []; self.redo_stack = []; self.start = None; self.draft = None

    def set_pages(self, pixmaps):
        """Replace the rendered document and arrange every page vertically."""
        self.pixmaps = pixmaps or []
        self.pixmap = self.pixmaps[self.current_page] if self.pixmaps and self.current_page < len(self.pixmaps) else QPixmap()
        self.relayout_pages()

    def relayout_pages(self):
        self.page_rects = []
        y = 28
        for pixmap in self.pixmaps:
            x = max(18, (self.width() - pixmap.width()) / 2)
            rect = QRectF(x, y, pixmap.width(), pixmap.height())
            self.page_rects.append(rect)
            y += pixmap.height() + self.page_spacing
        height = max(650, int(y + 12))
        width = max(700, max((int(rect.right() + 18) for rect in self.page_rects), default=700))
        self.setMinimumSize(width, height)

    def resizeEvent(self, event):
        self.relayout_pages(); super().resizeEvent(event)

    def page_rect(self):
        if self.page_rects and 0 <= self.current_page < len(self.page_rects):
            return self.page_rects[self.current_page]
        if self.pixmap.isNull(): return QRectF()
        return QRectF((self.width() - self.pixmap.width()) / 2, 28, self.pixmap.width(), self.pixmap.height())

    def page_at(self, point):
        for index, rect in enumerate(self.page_rects):
            if rect.contains(point): return index
        return -1

    def paintEvent(self, event):
        p = QPainter(self); p.fillRect(self.rect(), QColor("#0b1018")); rect = self.page_rect()
        if not self.pixmaps and rect.isNull():
            p.setPen(QColor("#f4f7fb")); p.setFont(QFont("Segoe UI", 24, QFont.Bold)); p.drawText(self.rect(), Qt.AlignCenter, "Kadir's PDF App\n\nBir PDF açarak başlayın")
            p.setPen(QColor("#8995a6")); p.setFont(QFont("Segoe UI", 10)); p.drawText(self.rect().adjusted(0, 100, 0, 0), Qt.AlignCenter, "Ctrl+O ile dosya açabilirsiniz."); return
        for page_index, (pixmap, page_rect) in enumerate(zip(self.pixmaps, self.page_rects)):
            p.fillRect(page_rect.adjusted(-8, -8, 8, 8), QColor("#05070b")); p.drawPixmap(page_rect.topLeft().toPoint(), pixmap)
            for item in self.annotations:
                if item.get("page", self.current_page) != page_index: continue
                if item["kind"] == "text":
                    style = item.get("style", {}); font = QFont(style.get("family", "Segoe UI"), style.get("size", 12)); font.setBold(style.get("bold", False)); font.setItalic(style.get("italic", False)); font.setUnderline(style.get("underline", False)); p.setFont(font); p.setPen(QColor(style.get("color", "#1467d8"))); p.drawText(item["point"], item["value"])
                elif item["kind"] == "draw": p.setPen(QPen(QColor("#236fe0"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.drawPolyline(item["points"])
                else:
                    color = QColor("#f4cc2f") if item["kind"] == "highlight" else QColor("#236fe0"); p.setPen(QPen(color, 2)); color.setAlpha(75 if item["kind"] == "highlight" else 0); p.setBrush(color); p.drawRect(item["rect"])
        if self.draft: p.setPen(QPen(QColor("#f4cc2f"), 2, Qt.DashLine)); p.drawRect(self.draft)

    def mousePressEvent(self, e):
        self.start = e.position().toPoint()
        clicked_page = self.page_at(self.start)
        if clicked_page >= 0:
            self.current_page = clicked_page; self.pixmap = self.pixmaps[clicked_page]
        if self.tool == "text": self.text_clicked.emit(self.start)
        if self.tool == "draw": self.points = [self.start]

    def mouseMoveEvent(self, e):
        point = e.position().toPoint()
        if self.tool == "draw" and self.start: self.points.append(point); self.update()
        elif self.tool in ("highlight", "shape") and self.start: self.draft = QRectF(self.start, point).normalized(); self.update()

    def mouseReleaseEvent(self, e):
        point = e.position().toPoint()
        if self.tool == "draw" and len(getattr(self, "points", [])) > 1: self.add_annotation({"kind": "draw", "points": self.points[:]}); self.annotation_added.emit("Çizim eklendi")
        elif self.tool in ("highlight", "shape") and self.start:
            rect = QRectF(self.start, point).normalized()
            if rect.width() > 4 and rect.height() > 4: self.add_annotation({"kind": self.tool, "rect": rect}); self.annotation_added.emit("Vurgu eklendi" if self.tool == "highlight" else "Şekil eklendi")
        self.start = self.draft = None; self.update()

    def add_annotation(self, item):
        item.setdefault("page", self.current_page); self.annotations.append(item); self.redo_stack.clear(); self.update()
    def undo(self):
        if self.annotations: self.redo_stack.append(self.annotations.pop()); self.update(); return True
        return False
    def redo(self):
        if self.redo_stack: self.annotations.append(self.redo_stack.pop()); self.update(); return True
        return False
    def add_text(self, point, value, style=None): self.add_annotation({"kind": "text", "point": point, "value": value, "style": style or {}}); self.annotation_added.emit("Metin eklendi"); self.update()


class PDFStudio(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Kadir's PDF App"); self.resize(1480, 900); self.setMinimumSize(1150, 720)
        self.current_file = None; self.doc = None; self.page_index = 0; self.page_count = 1; self.zoom = 1.; self.recent = []; self.starred = []; self.active_tool = "select"; self.tts = None
        self.autosave_enabled = True; self.close_after_export = False
        self.text_style = {"family": "Segoe UI", "size": 12, "bold": False, "italic": False, "underline": False, "color": "#1467d8", "align": "left"}
        self.current_theme = "dark"
        self.reminder_store = ReminderStore(); self.load_state(); self.build_ui(); self.side = self.findChild(QFrame, "side"); self.right_panel = self.findChild(QFrame, "right"); self.install_header_controls(); self.build_editor_tab(); self.build_note_export_tab(); self.build_ai_tab(); self.apply_theme(); self.build_actions(); self.update_ui(); self.show_home(); self.reminder_timer = QTimer(self); self.reminder_timer.timeout.connect(self.check_reminders); self.reminder_timer.start(30000); QTimer.singleShot(1200, self.check_reminders)
        if os.environ.get("PDF_STUDIO_TEST_MODE") != "1": self.start_ollama_setup()

    def install_header_controls(self):
        header = self.findChild(QFrame, "header")
        if not header: return
        layout = header.layout(); self.sidebar_toggle = QPushButton("☰"); self.sidebar_toggle.setToolTip("Sol menüyü aç/kapat"); self.sidebar_toggle.clicked.connect(self.toggle_sidebar); layout.insertWidget(0, self.sidebar_toggle)
        logo_path = APP_DIR / "kadir_pdf_logo.png"; self.logo_widget = QLabel()
        if logo_path.exists(): self.logo_widget.setPixmap(QPixmap(str(logo_path)).scaled(185, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else: self.logo_widget.setText("Kadir's PDF App")
        self.logo_widget.setToolTip("Kadir's PDF App"); layout.insertWidget(1, self.logo_widget)
        self.right_toggle = QPushButton("▣"); self.right_toggle.setToolTip("Sağ paneli aç/kapat"); self.right_toggle.clicked.connect(self.toggle_right_panel); layout.addWidget(self.right_toggle)

    def toggle_sidebar(self):
        if not self.side: return
        visible = self.side.isVisible(); self.side.setVisible(not visible); self.sidebar_toggle.setText("☰" if visible else "×"); self.sidebar_toggle.setToolTip("Sol menüyü aç" if visible else "Sol menüyü kapat")

    def toggle_right_panel(self):
        if not self.right_panel: return
        visible = self.right_panel.isVisible(); self.right_panel.setVisible(not visible); self.right_toggle.setText("▣" if visible else "×"); self.right_toggle.setToolTip("Sağ paneli aç" if visible else "Sağ paneli kapat")

    def apply_theme(self):
        self.setStyleSheet("""
        QWidget{background:#101522;color:#f3f6fc;font-family:'Segoe UI';font-size:10pt}
        QLabel{background:transparent;color:#f3f6fc} QFrame#header,QFrame#side,QFrame#right,QStatusBar{background:#171d2d}
        QFrame#tool{background:#20283b;border:1px solid #3a4660;border-radius:10px} QLabel#muted{color:#a8b2c5} QLabel#section{background:transparent;color:#c0c9dc;font-size:9pt;font-weight:700}
        QPushButton{background:transparent;color:#c3cbda;border:0;border-radius:8px;padding:8px 10px} QPushButton:hover{background:#303d5a;color:white}
        QPushButton#primary{background:#8b7cff;color:#101522;font-weight:700} QPushButton#primary:hover{background:#a79cff}
        QPushButton#nav{text-align:left;padding:10px 14px} QPushButton#nav:checked,QPushButton#tool:checked{background:#41406f;color:white}
        QPushButton#tool{font-size:12pt;min-width:34px} QLineEdit,QSpinBox,QTextEdit,QComboBox{background:#252e43;border:1px solid #43516c;border-radius:8px;padding:7px;color:#f7f9fd;selection-background-color:#655bd0}
        QLineEdit:focus,QSpinBox:focus,QTextEdit:focus,QComboBox:focus{border:1px solid #9a8fff;background:#2b3550}
        QComboBox QAbstractItemView{background:#252e43;color:#f7f9fd;selection-background-color:#514a9a;border:1px solid #43516c}
        QTabWidget::pane{background:#171d2d;border:0} QTabBar::tab{background:transparent;padding:10px 9px;color:#a8b2c5} QTabBar::tab:hover{color:#f3f6fc} QTabBar::tab:selected{color:white;border-bottom:2px solid #9a8fff}
        QStatusBar{color:#a8b2c5;border-top:1px solid #303c55} QSplitter::handle{background:#303c55} QScrollArea{border:0;background:#101522}
        """)

    def build_menubar(self):
        file_menu = self.menuBar().addMenu("Dosya")
        file_menu.addAction("PDF aç", self.open_file, "Ctrl+O")
        file_menu.addAction("PDF dışa aktar", self.export_file, "Ctrl+S")
        file_menu.addAction("Yazdır", self.print_file, "Ctrl+P")
        file_menu.addSeparator(); file_menu.addAction("Çıkış", self.close, "Alt+F4")
        workspace_menu = self.menuBar().addMenu("Çalışma alanı")
        workspace_menu.addAction("Ana sayfa", lambda: self.navigate("Ana sayfa"))
        workspace_menu.addAction("Son kullanılanlar", lambda: self.navigate("Son kullanılanlar"))
        workspace_menu.addAction("Yıldızlı", self.open_starred_documents)
        workspace_menu.addAction("Açık PDF'yi yıldızla", self.toggle_star_current)
        workspace_menu.addAction("Açık PDF dosya yolunu kopyala", self.share_current)
        workspace_menu.addAction("Paylaşılanlar", lambda: self.navigate("Paylaşılanlar"))
        workspace_menu.addSeparator(); workspace_menu.addAction("Belgelerim", lambda: self.navigate("Belgelerim")); workspace_menu.addAction("Tarama gelen kutusu", lambda: self.navigate("Tarama gelen kutusu"))
        view_menu = self.menuBar().addMenu("Görünüm")
        theme_menu = view_menu.addMenu("Tema")
        theme_menu.addAction("Windows 11 siyah", lambda: self.set_theme("dark"))
        theme_menu.addAction("Beyaz", lambda: self.set_theme("light"))
        theme_menu.addAction("Yumuşak açık", lambda: self.set_theme("soft"))
        view_menu.addSeparator(); view_menu.addAction("Sol paneli aç/kapat", self.toggle_sidebar); view_menu.addAction("Sağ paneli aç/kapat", self.toggle_right_panel); view_menu.addAction("Tam ekran", self.toggle_fullscreen, "F11"); view_menu.addAction("Ollama hazırlığını durdur", self.stop_ollama_setup)
        voice_menu = self.menuBar().addMenu("Ses"); voice_menu.addAction("Vosk ile sesli metin", self.open_voice_to_text_dialog)
        calendar_menu = self.menuBar().addMenu("Takvim"); calendar_menu.addAction("Takvimi aç", self.open_calendar); calendar_menu.addAction("Bugünün notlarını kontrol et", self.check_reminders)
        qwen_menu = self.menuBar().addMenu("Qwen AI"); qwen_menu.addAction("Türkçe fine-tuning hazırlığı", self.open_finetune_dialog)
        help_menu = self.menuBar().addMenu("Yardım"); help_menu.addAction("Klavye kısayolları", self.show_help); help_menu.addAction("Hakkında", lambda: QMessageBox.information(self, "Kadir's PDF App", "Kadir's PDF App\nYerel PDF çalışma alanı")); self.build_panel_menubar()

    def start_ollama_setup(self):
        self.ollama_setup = OllamaSetupWorker(self); self.ollama_setup.status.connect(self.status_msg); self.ollama_setup.completed.connect(self.status_msg); self.ollama_setup.failed.connect(self.ollama_setup_failed); self.ollama_setup.start()

    def stop_ollama_setup(self):
        if hasattr(self, "ollama_setup") and self.ollama_setup.isRunning(): self.ollama_setup.stop(); self.status_msg("Ollama hazırlığı durduruluyor…")

    def ollama_setup_failed(self, message):
        self.status_msg(message); self.ai_chat.append(f"<span style='color:#d96b6b'><b>Ollama:</b> {message}</span>")

    def set_theme(self, theme):
        self.current_theme = theme
        if theme == "dark":
            self.apply_theme(); self.status_msg("Windows 11 siyah tema etkin"); return
        if theme == "light":
            background, panel, card, field, text, muted, border, accent = "#f4f6fa", "#ffffff", "#ffffff", "#f0f3f8", "#202735", "#687386", "#dce2ec", "#5d55c7"
        else:
            background, panel, card, field, text, muted, border, accent = "#edf1f5", "#f7f9fb", "#ffffff", "#e8edf3", "#273142", "#687586", "#d7dfe9", "#4c78a8"
        self.setStyleSheet(f"""
        QWidget{{background:{background};color:{text};font-family:'Segoe UI';font-size:10pt}}
        QLabel{{background:transparent;color:{text}}} QFrame#header,QFrame#side,QFrame#right,QStatusBar{{background:{panel}}}
        QFrame#tool{{background:{card};border:1px solid {border};border-radius:10px}} QLabel#muted{{color:{muted}}} QLabel#section{{background:transparent;color:{muted};font-size:9pt;font-weight:700}}
        QPushButton{{background:transparent;color:{text};border:0;border-radius:8px;padding:8px 10px}} QPushButton:hover{{background:{field}}} QPushButton#primary{{background:{accent};color:white;font-weight:700}}
        QPushButton#nav{{text-align:left;padding:10px 14px}} QPushButton#nav:checked,QPushButton#tool:checked{{background:{accent};color:white}}
        QPushButton#tool{{font-size:12pt;min-width:34px}} QLineEdit,QSpinBox,QTextEdit,QComboBox{{background:{field};border:1px solid {border};border-radius:8px;padding:7px;color:{text};selection-background-color:{accent}}}
        QLineEdit:focus,QSpinBox:focus,QTextEdit:focus,QComboBox:focus{{border:1px solid {accent}}} QComboBox QAbstractItemView{{background:{panel};color:{text};selection-background-color:{accent};border:1px solid {border}}}
        QTabWidget::pane{{background:{panel};border:0}} QTabBar::tab{{background:transparent;padding:10px 9px;color:{muted}}} QTabBar::tab:selected{{color:{text};border-bottom:2px solid {accent}}}
        QMenuBar{{background:{panel};color:{text};padding:3px}} QMenuBar::item:selected,QMenu::item:selected{{background:{field}}} QMenu{{background:{panel};color:{text};border:1px solid {border}}}
        QStatusBar{{color:{muted};border-top:1px solid {border}}} QSplitter::handle{{background:{border}}} QScrollArea{{border:0;background:{background}}}
        """); self.status_msg({"light": "Beyaz tema etkin", "soft": "Yumuşak açık tema etkin"}[theme])

    def build_ui(self):
        self.build_menubar()
        self.setStatusBar(QStatusBar()); self.statusBar().showMessage("Hazır · Yerel çalışma alanı")
        header = QFrame(objectName="header"); header.setFixedHeight(68); hl = QHBoxLayout(header); hl.setContentsMargins(22, 0, 18, 0)
        hl.addWidget(QLabel("PRO")); hl.addSpacing(45)
        self.search = QLineEdit(); self.search.setPlaceholderText("Belgede ara…"); self.search.returnPressed.connect(self.search_document); hl.addWidget(self.search, 1); b = QPushButton("Ara"); b.clicked.connect(self.search_document); hl.addWidget(b); hl.addWidget(QLabel("AK"))

        side = QFrame(objectName="side"); side.setMinimumWidth(220); side.setMaximumWidth(250); sl = QVBoxLayout(side); sl.setContentsMargins(12, 24, 12, 16); sl.addWidget(QLabel("ÇALIŞMA ALANI", objectName="section")); self.nav_group = QButtonGroup(self)
        for text in ("Ana sayfa", "Son kullanılanlar", "Yıldızlı", "Paylaşılanlar"): sl.addWidget(self.nav_button(text))
        sl.addSpacing(22); sl.addWidget(QLabel("DOSYALAR", objectName="section"))
        for text in ("Belgelerim", "Tarama gelen kutusu"): sl.addWidget(self.nav_button(text))
        sl.addSpacing(18); sl.addWidget(QLabel("DESTEK", objectName="section"))
        for text in ("Yardım merkezi", "Ayarlar"): sl.addWidget(self.nav_button(text))
        sl.addStretch(); sl.addWidget(QLabel("☁  Bulut senkronizasyonu\n    Güncel · 2.4 GB boş", objectName="muted"))

        center = QWidget(); cl = QVBoxLayout(center); cl.setContentsMargins(18, 0, 18, 18); row = QHBoxLayout(); self.file_title = QLabel(); self.file_title.setStyleSheet("font-size:14pt;font-weight:700"); row.addWidget(self.file_title); self.file_meta = QLabel(objectName="muted"); row.addWidget(self.file_meta); row.addStretch(); open_btn = QPushButton("＋  PDF aç", objectName="primary"); open_btn.clicked.connect(self.open_file); row.addWidget(open_btn); cl.addLayout(row)
        self.document_header = row
        tool = QFrame(objectName="tool"); tool.setFixedHeight(54); self.tool_bar = tool; tl = QHBoxLayout(tool); tl.setContentsMargins(8, 4, 8, 4); self.tool_group = QButtonGroup(self)
        for txt, name in (("↖", "select"), ("T", "text"), ("✎", "draw"), ("▰", "highlight"), ("□", "shape")):
            x = QPushButton(txt, objectName="tool"); x.setCheckable(True); x.clicked.connect(lambda checked, n=name: self.set_tool(n)); self.tool_group.addButton(x); tl.addWidget(x); x.setChecked(name == "select")
        tl.addWidget(QLabel("Sayfa", objectName="muted")); self.page_spin = QSpinBox(); self.page_spin.setRange(1, 1); self.page_spin.valueChanged.connect(self.go_page); tl.addWidget(self.page_spin); self.total_label = QLabel("/ 1", objectName="muted"); tl.addWidget(self.total_label); tl.addWidget(self.btn("‹", lambda: self.change_page(-1))); tl.addWidget(self.btn("›", lambda: self.change_page(1))); tl.addWidget(self.btn("−", lambda: self.change_zoom(-.1))); self.zoom_label = QLabel("100%"); tl.addWidget(self.zoom_label); tl.addWidget(self.btn("＋", lambda: self.change_zoom(.1))); tl.addWidget(self.btn("Fit", self.fit_page)); tl.addStretch(); tl.addWidget(self.btn("⛶", self.toggle_fullscreen)); cl.addWidget(tool)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop); self.document_view = scroll; self.canvas = PageCanvas(); self.canvas.text_clicked.connect(self.prompt_text); self.canvas.annotation_added.connect(self.status_msg); scroll.setWidget(self.canvas); cl.addWidget(scroll, 1)
        self.dashboard = self.build_dashboard(); cl.addWidget(self.dashboard, 1); self.dashboard.hide()

        right = QFrame(objectName="right"); right.setMinimumWidth(315); right.setMaximumWidth(390); rl = QVBoxLayout(right); rl.setContentsMargins(18, 24, 18, 16); rl.addWidget(QLabel("ÇALIŞMA PANELİ", objectName="section")); self.tabs = QTabWidget(); rl.addWidget(self.tabs, 1); self.build_tabs()
        splitter = QSplitter(Qt.Horizontal); splitter.addWidget(side); splitter.addWidget(center); splitter.addWidget(right); splitter.setStretchFactor(1, 1); splitter.setSizes([230, 900, 340]); root = QWidget(); layout = QVBoxLayout(root); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0); layout.addWidget(header); layout.addWidget(splitter, 1); self.setCentralWidget(root)

    def build_dashboard(self):
        dashboard = QWidget(); layout = QVBoxLayout(dashboard); layout.setContentsMargins(18, 18, 18, 12)
        welcome = QLabel("Genel istatistikler"); welcome.setStyleSheet("font-size:22pt;font-weight:700;"); layout.addWidget(welcome)
        subtitle = QLabel("Kadir's PDF App çalışma alanınıza genel bakış", objectName="muted"); layout.addWidget(subtitle); layout.addSpacing(18)
        cards = QHBoxLayout(); self.stat_cards = {}
        for key, title in (("files", "Açılan PDF"), ("pages", "Toplam sayfa"), ("notes", "Not karakteri"), ("words", "Çıkarılan kelime")):
            card = QFrame(); card.setStyleSheet("QFrame{background:#20283b;border:0;border-radius:12px} QLabel{border:0;background:transparent}"); box = QVBoxLayout(card); label = QLabel(title, objectName="muted"); label.setStyleSheet("border:0;background:transparent"); value = QLabel("0"); value.setStyleSheet("font-size:22pt;font-weight:700;color:#a79cff;border:0;background:transparent"); box.addWidget(label); box.addWidget(value); self.stat_cards[key] = value; cards.addWidget(card)
        layout.addLayout(cards); layout.addSpacing(18)
        recent_title = QLabel("Son aktivite"); recent_title.setStyleSheet("font-size:13pt;font-weight:700"); layout.addWidget(recent_title); self.dashboard_activity = QLabel(objectName="muted"); self.dashboard_activity.setWordWrap(True); layout.addWidget(self.dashboard_activity)
        layout.addStretch(); open_button = QPushButton("＋  İlk PDF'i aç", objectName="primary"); open_button.clicked.connect(self.open_file); layout.addWidget(open_button, 0, Qt.AlignLeft); return dashboard

    def build_panel_menubar(self):
        panel_root = self.menuBar().addMenu("Çalışma paneli"); panel_menu = panel_root.addMenu("Panel")
        panel_menu.addAction("Dosya bilgileri", lambda: self.tabs.setCurrentIndex(0))
        panel_menu.addAction("Notlar", lambda: self.tabs.setCurrentIndex(1))
        panel_menu.addAction("PDF metni", lambda: self.tabs.setCurrentIndex(2))
        panel_menu.addAction("Yer imleri", lambda: self.tabs.setCurrentIndex(3))
        editor_menu = panel_root.addMenu("Düzenle")
        editor_menu.addAction("Metin editörü", lambda: self.tabs.setCurrentIndex(4))
        editor_menu.addAction("Not dışa aktar", lambda: self.tabs.setCurrentIndex(5))
        editor_menu.addAction("AI Asistan", lambda: self.tabs.setCurrentIndex(6))
        actions = panel_root.addMenu("İşlemler")
        actions.addAction("PDF aç", self.open_file)
        actions.addAction("Notları sesli oku", self.read_notes)
        actions.addAction("PDF'yi özetle", lambda: (self.tabs.setCurrentIndex(6), self.ask_ai("Bu PDF'yi kısa ve anlaşılır maddeler halinde özetle.")))

    def build_tabs(self):
        info = QWidget(); il = QVBoxLayout(info); self.info_label = QLabel(); self.info_label.setWordWrap(True); il.addWidget(self.info_label); il.addStretch(); self.tabs.addTab(info, "Dosya")
        self.notes = QTextEdit(); self.notes.setPlaceholderText("PDF açıkken notlarınızı buraya yazabilirsiniz…"); self.notes.textChanged.connect(self.save_notes); np = QWidget(); nl = QVBoxLayout(np); nl.addWidget(QLabel("Notlar otomatik olarak kaydedilir.", objectName="muted")); nl.addWidget(self.notes, 1); speak = QPushButton("🔊  Notları sesli oku", objectName="primary"); speak.clicked.connect(self.read_notes); nl.addWidget(speak); stop = QPushButton("Durdur"); stop.clicked.connect(self.stop_speech); nl.addWidget(stop); self.tabs.addTab(np, "Notlar")
        self.extracted = QTextEdit(); self.extracted.setReadOnly(True); self.extracted.setPlaceholderText("PDF metni burada görünecek."); ep = QWidget(); el = QVBoxLayout(ep); el.addWidget(self.extracted, 1); cp = QPushButton("Metni panoya kopyala"); cp.clicked.connect(lambda: QApplication.clipboard().setText(self.extracted.toPlainText())); el.addWidget(cp); self.tabs.addTab(ep, "PDF metni")
        bm = QTextEdit("▸  Özet\n▸  Giriş\n▸  Kullanım koşulları\n▸  Ekler"); bm.setReadOnly(True); self.tabs.addTab(bm, "Yer imleri")

    def build_editor_tab(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.addWidget(QLabel("Metin editörü", objectName="muted"))
        controls = QHBoxLayout(); self.font_combo = QComboBox(); self.font_combo.addItems(["Segoe UI", "Arial", "Calibri", "Times New Roman", "Courier New"]); self.font_combo.currentTextChanged.connect(self.set_text_family); controls.addWidget(self.font_combo, 1)
        self.font_size = QSpinBox(); self.font_size.setRange(6, 72); self.font_size.setValue(12); self.font_size.setSuffix(" pt"); self.font_size.valueChanged.connect(self.set_text_size); controls.addWidget(self.font_size); layout.addLayout(controls)
        style_row = QHBoxLayout(); self.bold_button = self.style_button("B", self.toggle_bold, "Kalın"); self.italic_button = self.style_button("I", self.toggle_italic, "İtalik"); self.underline_button = self.style_button("U", self.toggle_underline, "Altı çizili")
        style_row.addWidget(self.bold_button); style_row.addWidget(self.italic_button); style_row.addWidget(self.underline_button)
        for label, align in (("Sol", "left"), ("Orta", "center"), ("Sağ", "right")): style_row.addWidget(self.style_button(label, lambda checked=False, a=align: self.set_text_alignment(a), f"{label} hizala"))
        self.color_button = QPushButton("●  Renk"); self.color_button.clicked.connect(self.choose_text_color); style_row.addWidget(self.color_button); layout.addLayout(style_row)
        self.rich_notes = QTextEdit(); self.rich_notes.setPlaceholderText("Biçimlendirilmiş metninizi buraya yazın…"); layout.addWidget(self.rich_notes, 1); self.tabs.addTab(page, "Metin editörü")

    def build_note_export_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Notları farklı formatlarda kaydedin", objectName="muted"))
        layout.addWidget(QLabel("Aktarım kaynağı: Notlar sekmesindeki metin", objectName="muted"))
        for label, extension, description in (("PDF olarak kaydet", "pdf", "PDF dosyaları (*.pdf)"), ("Word olarak kaydet", "docx", "Word dosyaları (*.docx)"), ("Excel olarak kaydet", "xlsx", "Excel dosyaları (*.xlsx)"), ("TXT olarak kaydet", "txt", "Metin dosyaları (*.txt)")):
            button = QPushButton(label); button.clicked.connect(lambda checked=False, ext=extension, desc=description: self.export_notes(ext, desc)); layout.addWidget(button)
        layout.addStretch(); self.tabs.addTab(page, "Not dışa aktar")

    def build_ai_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Qwen 2.5 belge asistanı", objectName="muted"))
        model_row = QHBoxLayout(); model_row.addWidget(QLabel("Model", objectName="muted")); self.ai_model = QLineEdit("qwen2.5:7b"); self.ai_model.setToolTip("Ollama'da kurulu model adını yazın"); model_row.addWidget(self.ai_model); layout.addLayout(model_row)
        self.ai_chat = QTextEdit(); self.ai_chat.setReadOnly(True); self.ai_chat.setPlaceholderText("Qwen yanıtları burada görünecek."); layout.addWidget(self.ai_chat, 1)
        self.ai_prompt = QLineEdit(); self.ai_prompt.setPlaceholderText("PDF hakkında bir soru yazın…"); self.ai_prompt.returnPressed.connect(self.ask_ai); layout.addWidget(self.ai_prompt)
        buttons = QHBoxLayout(); ask = QPushButton("Gönder", objectName="primary"); ask.clicked.connect(self.ask_ai); buttons.addWidget(ask); summary = QPushButton("PDF'yi özetle"); summary.clicked.connect(lambda: self.ask_ai("Bu PDF'yi kısa ve anlaşılır maddeler halinde özetle.")); buttons.addWidget(summary); explain = QPushButton("Notları düzenle"); explain.clicked.connect(lambda: self.ask_ai("Notlarımı daha düzenli ve anlaşılır hale getir.")); buttons.addWidget(explain); layout.addLayout(buttons)
        self.tabs.addTab(page, "AI Asistan")

    def ai_context(self):
        pdf_text = self.extracted.toPlainText().strip() if hasattr(self, "extracted") else ""
        notes = self.notes.toPlainText().strip() if hasattr(self, "notes") else ""
        if len(pdf_text) > 14000: pdf_text = pdf_text[:14000] + "\n[PDF metni kısaltıldı]"
        if len(notes) > 5000: notes = notes[:5000] + "\n[Notlar kısaltıldı]"
        return f"PDF METNİ:\n{pdf_text or '[PDF metni yok]'}\n\nKULLANICI NOTLARI:\n{notes or '[Not yok]'}"

    def ask_ai(self, preset=None):
        question = preset or self.ai_prompt.text().strip()
        if not question: self.status_msg("AI asistanına bir soru yazın"); return
        if hasattr(self, "ai_worker") and self.ai_worker.isRunning(): self.status_msg("Önceki AI yanıtı hazırlanıyor"); return
        model = self.ai_model.text().strip() or "qwen2.5:7b"; self.ai_prompt.clear(); self.ai_chat.append(f"<b>Sen:</b> {question}"); self.ai_chat.append("<i>Qwen düşünüyor…</i>"); self.status_msg("Ollama Qwen yanıtı hazırlanıyor")
        system_prompt = getattr(self, "qwen_system_prompt", "Türkçe cevap ver. PDF bağlamına sadık kal; bilgi uydurma.")
        examples = "\n\n".join(f"ÖRNEK KULLANICI: {item.get('user', '')[:1200]}\nÖRNEK ASİSTAN: {item.get('assistant', '')[:1200]}" for item in getattr(self, "qwen_finetune_examples", [])[:8])
        prompt = f"{system_prompt}\n\nSen Kadir's PDF App içindeki Türkçe belge asistanısın. Yanıtlarını net, mantıklı ve kısa ver.\n\n{self.ai_context()}\n\n{examples}\n\nSORU:\n{question}"
        self.ai_worker = OllamaWorker(model, prompt, self); self.ai_worker.completed.connect(self.ai_completed); self.ai_worker.failed.connect(self.ai_failed); self.ai_worker.start()

    def ai_completed(self, response):
        safe_response = escape(response).replace("\n", "<br>"); self.ai_chat.append(f"<b>Qwen:</b> {safe_response}"); self.status_msg("Qwen yanıtı hazır")

    def ai_failed(self, message):
        self.ai_chat.append(f"<span style='color:#d96b6b'><b>AI:</b> {escape(message)}</span>"); self.status_msg("AI bağlantısı kurulamadı")

    def style_button(self, text, slot, tip):
        button = QPushButton(text); button.setCheckable(text in ("B", "I", "U")); button.setToolTip(tip); button.clicked.connect(slot); return button

    def apply_rich_format(self):
        from PySide6.QtGui import QTextCharFormat
        fmt = QTextCharFormat(); fmt.setFontFamily(self.text_style["family"]); fmt.setFontPointSize(self.text_style["size"]); fmt.setFontWeight(QFont.Bold if self.text_style["bold"] else QFont.Normal); fmt.setFontItalic(self.text_style["italic"]); fmt.setFontUnderline(self.text_style["underline"]); fmt.setForeground(QColor(self.text_style["color"]))
        cursor = self.rich_notes.textCursor(); cursor.mergeCharFormat(fmt); self.rich_notes.setTextCursor(cursor); self.rich_notes.setCurrentCharFormat(fmt); self.rich_notes.setAlignment({"left": Qt.AlignLeft, "center": Qt.AlignCenter, "right": Qt.AlignRight}[self.text_style["align"]])

    def set_text_family(self, family): self.text_style["family"] = family; self.apply_rich_format()
    def set_text_size(self, size): self.text_style["size"] = size; self.apply_rich_format()
    def toggle_bold(self): self.text_style["bold"] = self.bold_button.isChecked(); self.apply_rich_format()
    def toggle_italic(self): self.text_style["italic"] = self.italic_button.isChecked(); self.apply_rich_format()
    def toggle_underline(self): self.text_style["underline"] = self.underline_button.isChecked(); self.apply_rich_format()
    def set_text_alignment(self, align): self.text_style["align"] = align; self.apply_rich_format()
    def choose_text_color(self):
        color = QColorDialog.getColor(QColor(self.text_style["color"]), self, "Metin rengi")
        if color.isValid(): self.text_style["color"] = color.name(); self.color_button.setStyleSheet(f"color:{color.name()};"); self.apply_rich_format()

    def export_notes(self, extension, description):
        text = self.notes.toPlainText().strip()
        if not text:
            self.status_msg("Dışa aktarmak için önce Notlar sekmesine metin yazın")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Notları kaydet", f"notlar.{extension}", description)
        if not path:
            return
        try:
            if extension == "txt":
                Path(path).write_text(text, encoding="utf-8")
            elif extension == "pdf":
                self.write_notes_pdf(path, text)
            elif extension == "docx":
                self.write_notes_docx(path, text)
            elif extension == "xlsx":
                self.write_notes_xlsx(path, text)
            self.status_msg(f"Notlar {extension.upper()} olarak kaydedildi")
        except ImportError as exc:
            QMessageBox.warning(self, "Eksik paket", f"Bu format için gerekli paket kurulu değil:\n{exc}\n\nKurulum: pip install python-docx openpyxl")
        except Exception as exc:
            QMessageBox.critical(self, "Notları kaydetme", str(exc))

    def write_notes_pdf(self, path, text):
        if not fitz:
            raise ImportError("PDF aktarımı için PyMuPDF gerekir (pip install PyMuPDF)")
        document = fitz.open(); lines = []
        for paragraph in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(paragraph, width=88, break_long_words=False, break_on_hyphens=False) or [""])
        font_path = next((candidate for candidate in (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/arial.ttf")) if candidate.exists()), None)
        lines_per_page = 40
        for start in range(0, len(lines), lines_per_page):
            page = document.new_page(width=595, height=842)
            for row, line in enumerate(lines[start:start + lines_per_page]):
                point = (50, 58 + row * 18)
                if font_path:
                    page.insert_text(point, line, fontsize=11, fontfile=str(font_path), color=(0.1, 0.12, 0.16))
                else:
                    page.insert_text(point, line, fontsize=11, fontname="helv", color=(0.1, 0.12, 0.16))
        document.save(path); document.close()

    def write_notes_docx(self, path, text):
        from docx import Document
        document = Document(); document.add_heading("Kadir's PDF App Notları", 0)
        for paragraph in text.splitlines() or [""]:
            document.add_paragraph(paragraph)
        document.save(path)

    def write_notes_xlsx(self, path, text):
        from openpyxl import Workbook
        workbook = Workbook(); sheet = workbook.active; sheet.title = "Notlar"; sheet["A1"] = "Kadir's PDF App Notları"
        for row, line in enumerate(text.splitlines() or [""], start=2): sheet.cell(row=row, column=1, value=line)
        sheet.column_dimensions["A"].width = 100; workbook.save(path)

    def build_actions(self):
        for name, shortcut, slot in (("PDF aç", "Ctrl+O", self.open_file), ("Ara", "Ctrl+F", self.search.setFocus), ("Dışa aktar", "Ctrl+S", self.export_file), ("Seçim", "Escape", lambda: self.set_tool("select"))):
            a = QAction(name, self); a.setShortcut(QKeySequence(shortcut)); a.triggered.connect(slot); self.addAction(a)
        undo = QAction("Geri al", self); undo.setShortcut(QKeySequence("Ctrl+Z")); undo.triggered.connect(self.undo_annotation); self.addAction(undo)
        redo = QAction("Yinele", self); redo.setShortcut(QKeySequence("Ctrl+Y")); redo.triggered.connect(self.redo_annotation); self.addAction(redo)

    def show_home(self):
        self.tool_bar.setVisible(False); self.document_view.setVisible(False); self.dashboard.setVisible(True); self.update_dashboard(); self.status_msg("Ana sayfa açıldı")

    def show_document(self):
        self.tool_bar.setVisible(True); self.document_view.setVisible(True); self.dashboard.setVisible(False)

    def update_dashboard(self):
        if not hasattr(self, "stat_cards"): return
        total_pages = len(self.doc) if self.doc else 0; note_count = len(self.notes.toPlainText()) if hasattr(self, "notes") else 0; word_count = len(self.extracted.toPlainText().split()) if hasattr(self, "extracted") else 0
        self.stat_cards["files"].setText(str(len(self.recent))); self.stat_cards["pages"].setText(str(total_pages)); self.stat_cards["notes"].setText(str(note_count)); self.stat_cards["words"].setText(str(word_count))
        last = Path(self.recent[0]).name if self.recent else "Henüz PDF açılmadı"
        self.dashboard_activity.setText(f"Son açılan dosya: {last}\n\nNotlar otomatik olarak yerel çalışma alanına kaydedilir. PDF açarak sayfa ve metin istatistiklerini güncelleyebilirsiniz.")
    def nav_button(self, text):
        b = QPushButton(text, objectName="nav"); b.setCheckable(True); b.clicked.connect(lambda: self.navigate(text)); self.nav_group.addButton(b); b.setChecked(text == "Ana sayfa"); return b
    def btn(self, text, slot): b = QPushButton(text); b.setFixedHeight(34); b.clicked.connect(slot); return b
    def load_state(self):
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.recent = state.get("recent", [])
            self.starred = state.get("starred", [])
            self.saved_notes = state.get("notes", "")
            self.qwen_finetune_examples = state.get("qwen_finetune_examples", [])
            self.qwen_system_prompt = state.get("qwen_system_prompt", "Türkçe cevap ver. PDF bağlamına sadık kal; bilgi uydurma. Cevaplarını mantıklı, açık ve gerektiğinde maddeli yaz.")
            self.vosk_model_path = state.get("vosk_model_path", bundled_vosk_model_path())
            self.autosave_enabled = bool(state.get("autosave_enabled", True))
            self.close_after_export = bool(state.get("close_after_export", False))
        except Exception:
            self.recent = []; self.starred = []
            self.saved_notes = ""
            self.qwen_finetune_examples = []
            self.qwen_system_prompt = "Türkçe cevap ver. PDF bağlamına sadık kal; bilgi uydurma. Cevaplarını mantıklı, açık ve gerektiğinde maddeli yaz."
            self.vosk_model_path = bundled_vosk_model_path()
            self.autosave_enabled = True
            self.close_after_export = False
    def save_state(self):
        try: STATE_FILE.write_text(json.dumps({"recent": self.recent[:12], "starred": self.starred[:100], "notes": self.notes.toPlainText(), "qwen_finetune_examples": self.qwen_finetune_examples[:1000], "qwen_system_prompt": self.qwen_system_prompt, "vosk_model_path": self.vosk_model_path, "autosave_enabled": self.autosave_enabled, "close_after_export": self.close_after_export}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception: pass
    def save_notes(self):
        if hasattr(self, "notes"):
            if self.autosave_enabled: self.save_state()
            self.update_dashboard()
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "PDF aç", "", "PDF dosyaları (*.pdf);;Tüm dosyalar (*)")
        if path: self.load_file(Path(path))
    def open_finetune_dialog(self):
        dialog = QwenFineTuningDialog(self, self)
        dialog.exec()
    def open_voice_to_text_dialog(self):
        dialog = VoiceToTextDialog(self, self)
        dialog.exec()
    def open_calendar(self):
        dialog = CalendarDialog(self, self)
        dialog.exec()
    def check_reminders(self):
        due = self.reminder_store.due(datetime.now())
        if not due: return
        self.reminder_store.mark_notified([item[0] for item in due]); QApplication.beep()
        message = "\n\n".join(f"{reminder_date} {reminder_time}\n{note}" for _, reminder_date, reminder_time, note in due)
        QMessageBox.information(self, "Takvim hatırlatıcısı", message)
    def load_file(self, path):
        if self.doc:
            self.doc.close()
        self.current_file = path; self.doc = None; self.page_count = 1; self.canvas.annotations.clear(); self.canvas.redo_stack.clear()
        if str(path) not in self.recent: self.recent.insert(0, str(path))
        if fitz:
            try:
                self.doc = fitz.open(str(path)); self.page_count = len(self.doc); chunks = []; total_chars = 0
                for page in self.doc:
                    remaining = 200_000 - total_chars
                    if remaining <= 0: break
                    chunk = page.get_text("text")[:remaining]; chunks.append(chunk); total_chars += len(chunk)
                extracted = "\n\n".join(chunks) or "Bu PDF içinde metin bulunamadı."
                if total_chars >= 200_000: extracted += "\n\n[Metin performans için 200.000 karakterle sınırlandı.]"
                self.extracted.setPlainText(extracted)
            except Exception as exc: self.status_msg(f"PDF okunamadı: {exc}")
        else: self.extracted.setPlainText("Metin çıkarma için PyMuPDF kurun: pip install PyMuPDF")
        self.page_index = 0; self.page_spin.setRange(1, self.page_count); self.page_spin.setValue(1); self.total_label.setText(f"/ {self.page_count}"); self.show_document(); self.render_page(); self.update_dashboard(); self.status_msg(f"{path.name} açıldı")
    def update_ui(self):
        self.file_title.setText(self.current_file.name if self.current_file else "Belge seçilmedi"); self.file_meta.setText("Yerel belge · Hazır" if self.current_file else "PDF çalışma alanınıza hoş geldiniz"); size = f"{self.current_file.stat().st_size / 1024:.0f} KB" if self.current_file and self.current_file.exists() else "—"; self.info_label.setText(f"Dosya\n{self.current_file.name if self.current_file else 'Belge seçilmedi'}\n\nSayfa\n{self.page_index + 1} / {self.page_count}\n\nBoyut\n{size}\n\nNotlar\nOtomatik kaydediliyor"); self.update_dashboard()
    def render_page(self):
        pixmaps = []
        if self.doc and fitz:
            # 1.35x maps the PDF's 72 DPI coordinate space more cleanly to a
            # modern display while preserving the existing zoom percentage.
            requested = max(.25, self.zoom) * 1.35
            for page in self.doc:
                rect = page.rect
                max_pixels = 20_000_000
                safe_scale = min(requested, math.sqrt(max_pixels / max(1.0, rect.width * rect.height)))
                raw = page.get_pixmap(matrix=fitz.Matrix(safe_scale, safe_scale), alpha=False, annots=True)
                img = QImage(raw.samples, raw.width, raw.height, raw.stride, QImage.Format_RGB888).copy()
                pixmaps.append(QPixmap.fromImage(img))
        self.canvas.current_page = self.page_index
        self.canvas.set_pages(pixmaps)
        self.canvas.update()
        self.zoom_label.setText(f"{int(self.zoom * 100)}%")
        self.update_ui()
    def change_page(self, delta): self.page_spin.setValue(max(1, min(self.page_count, self.page_index + 1 + delta)))
    def go_page(self, value): self.page_index = value - 1; self.render_page()
    def change_zoom(self, delta): self.zoom = max(.5, min(2.5, round(self.zoom + delta, 2))); self.render_page()
    def fit_page(self): self.zoom = 1.; self.render_page()
    def set_tool(self, tool): self.active_tool = tool; self.canvas.tool = tool; self.status_msg({"select":"Seçim aracı","text":"Metin kutusu: sayfaya tıklayın","draw":"Çizim aracı: sürükleyin","highlight":"Vurgu aracı: sürükleyin","shape":"Şekil aracı: sürükleyin"}[tool])
    def undo_annotation(self): self.status_msg("Son işlem geri alındı" if self.canvas.undo() else "Geri alınacak işlem yok")
    def redo_annotation(self): self.status_msg("İşlem yinelendi" if self.canvas.redo() else "Yinelenecek işlem yok")
    def prompt_text(self, point):
        text, ok = QInputDialog.getText(self, "Metin ekle", "Sayfaya eklenecek metin:")
        if ok and text.strip(): self.canvas.add_text(point, text.strip(), self.text_style.copy())
    def navigate(self, text):
        if text == "Ana sayfa":
            self.show_home(); return
        if text == "Son kullanılanlar":
            if not self.recent:
                QMessageBox.information(self, "Son kullanılanlar", "Henüz açılmış bir PDF bulunmuyor."); return
            selected, ok = QInputDialog.getItem(self, "Son kullanılanlar", "Açılacak dosya:", [Path(path).name for path in self.recent[:8]], 0, False)
            if ok and selected:
                path = next((path for path in self.recent if Path(path).name == selected), None)
                if path and Path(path).exists(): self.load_file(Path(path))
                else: QMessageBox.warning(self, "Dosya bulunamadı", "Bu dosya artık bilgisayarda bulunmuyor.")
            return
        if text in ("Belgelerim", "Tarama gelen kutusu"):
            path, _ = QFileDialog.getOpenFileName(self, text, str(Path.home()), "PDF dosyaları (*.pdf);;Tüm dosyalar (*)")
            if path: self.load_file(Path(path))
            return
        if text == "Yıldızlı": self.open_starred_documents(); return
        if text == "Paylaşılanlar": QMessageBox.information(self, "Paylaşılanlar", "Yerel çalışma modunda paylaşılan belge bulunmuyor."); return
        if text == "Yardım merkezi": self.show_help(); return
        if text == "Ayarlar": self.show_settings(); return
        self.status_msg(f"{text} bölümü seçildi")

    def show_help(self):
        QMessageBox.information(self, "Kadir's PDF App Yardım", "Ctrl+O: PDF aç\nCtrl+F: Belge ara\nCtrl+S: PDF dışa aktar\nEscape: Seçim aracına dön\n\nSağ panelden not alabilir, metin biçimlendirebilir ve notlarınızı PDF, Word, Excel veya TXT olarak kaydedebilirsiniz.")

    def toggle_star_current(self):
        if not self.current_file:
            self.status_msg("Yıldızlamak için önce bir PDF açın"); return
        path = str(self.current_file)
        if path in self.starred: self.starred.remove(path); self.status_msg("Belge yıldızlardan kaldırıldı")
        else: self.starred.insert(0, path); self.status_msg("Belge yıldızlara eklendi")
        self.save_state()

    def open_starred_documents(self):
        existing = [path for path in self.starred if Path(path).exists()]
        if not existing:
            QMessageBox.information(self, "Yıldızlı", "Henüz yıldızlı belge bulunmuyor."); return
        selected, ok = QInputDialog.getItem(self, "Yıldızlı", "Açılacak belge:", [Path(path).name for path in existing], 0, False)
        if ok and selected:
            self.load_file(Path(next(path for path in existing if Path(path).name == selected)))

    def share_current(self):
        if not self.current_file:
            self.status_msg("Paylaşmak için önce bir PDF açın"); return
        QApplication.clipboard().setText(str(self.current_file)); self.status_msg("Dosya yolu panoya kopyalandı")

    def show_settings(self):
        dialog = QDialog(self); dialog.setWindowTitle("Ayarlar"); dialog.setMinimumWidth(360); layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Kadir's PDF App ayarları")); autosave = QCheckBox("Notları otomatik kaydet"); autosave.setChecked(self.autosave_enabled); layout.addWidget(autosave)
        close_after = QCheckBox("Dışa aktardıktan sonra Dosya sekmesine dön"); close_after.setChecked(self.close_after_export); layout.addWidget(close_after)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.autosave_enabled = autosave.isChecked(); self.close_after_export = close_after.isChecked(); self.save_state(); self.status_msg("Ayarlar kaydedildi")

    def search_document(self):
        query = self.search.text().strip()
        if not query: self.status_msg("Aramak için metin yazın"); return
        if not self.doc: self.status_msg("Önce bir PDF açın"); return
        self.search_results = [(index, rect) for index, page in enumerate(self.doc) for rect in page.search_for(query)]
        if self.search_results:
            self.page_spin.setValue(self.search_results[0][0] + 1); self.status_msg(f'"{query}" için {len(self.search_results)} sonuç bulundu')
        else: self.status_msg(f'"{query}" için sonuç bulunamadı')
    def export_file(self):
        if not self.current_file: self.status_msg("Önce bir PDF açın"); return
        path, _ = QFileDialog.getSaveFileName(self, "PDF dışa aktar", "kopya.pdf", "PDF (*.pdf)")
        if path:
            try:
                if self.doc:
                    destination = Path(path); same_file = destination.resolve() == self.current_file.resolve(); temporary_path = None
                    if same_file:
                        temporary_path = Path(tempfile.mkstemp(suffix=".pdf")[1]); destination = temporary_path
                    export_doc = fitz.open(str(self.current_file)); self.apply_annotations(export_doc); export_doc.save(str(destination)); export_doc.close()
                    if temporary_path: os.replace(str(temporary_path), str(self.current_file))
                else: Path(path).write_bytes(self.current_file.read_bytes())
                if self.close_after_export: self.tabs.setCurrentIndex(0)
                self.status_msg("PDF dışa aktarıldı")
            except Exception as exc: QMessageBox.critical(self, "Dışa aktarma", str(exc))

    def apply_annotations(self, document):
        if not self.canvas.annotations or not document: return
        for item in self.canvas.annotations:
            page_index = item.get("page", self.page_index)
            if page_index < 0 or page_index >= len(document): continue
            page = document.load_page(page_index)
            page_rect = self.canvas.page_rects[page_index] if page_index < len(self.canvas.page_rects) else self.canvas.page_rect()
            scale = max(self.zoom * 1.35, 0.01)
            def point_to_pdf(point): return ((point.x() - page_rect.left()) / scale, (point.y() - page_rect.top()) / scale)
            if item["kind"] == "text":
                x, y = point_to_pdf(item["point"]); style = item.get("style", {}); color = QColor(style.get("color", "#1467d8")); page.insert_text((x, y), item["value"], fontsize=style.get("size", 12), fontname="helv", color=(color.redF(), color.greenF(), color.blueF()))
            elif item["kind"] in ("highlight", "shape"):
                rect = item["rect"]; x1, y1 = point_to_pdf(rect.topLeft()); x2, y2 = point_to_pdf(rect.bottomRight()); pdf_rect = fitz.Rect(x1, y1, x2, y2)
                if item["kind"] == "highlight": page.draw_rect(pdf_rect, color=(0.95, 0.75, 0.1), fill=(1.0, 0.85, 0.2), fill_opacity=0.3, width=1)
                else: page.draw_rect(pdf_rect, color=(0.14, 0.43, 0.88), width=1.5)
            elif item["kind"] == "draw": page.draw_polyline([point_to_pdf(point) for point in item["points"]], color=(0.14, 0.43, 0.88), width=2)
    def print_file(self): self.status_msg("Yazdırma isteği işletim sistemine gönderiliyor"); os.startfile(str(self.current_file), "print") if self.current_file and sys.platform.startswith("win") else None
    def toggle_fullscreen(self): self.showNormal() if self.isFullScreen() else self.showFullScreen()
    def status_msg(self, text): self.statusBar().showMessage(text)
    def read_notes(self):
        text = self.notes.toPlainText().strip()
        if not text: self.status_msg("Önce not yazın"); return
        if not pyttsx3: self.status_msg("Seslendirme için pyttsx3 kurun: pip install pyttsx3"); return
        if hasattr(self, "tts_worker") and self.tts_worker.isRunning(): self.tts_worker.stop(); self.tts_worker.wait(500)
        self.tts_worker = SpeechWorker(text, self); self.tts_worker.status.connect(self.status_msg); self.tts_worker.completed.connect(lambda: self.status_msg("Not seslendirmesi tamamlandı")); self.tts_worker.failed.connect(self.status_msg); self.tts_worker.start(); self.status_msg("Notlar arka planda seslendiriliyor")
    def stop_speech(self):
        if hasattr(self, "tts_worker") and self.tts_worker.isRunning(): self.tts_worker.stop(); self.tts_worker.requestInterruption(); self.status_msg("Seslendirme durduruldu")

    def closeEvent(self, event):
        if getattr(self, "doc", None): self.doc.close(); self.doc = None
        if hasattr(self, "tts_worker") and self.tts_worker.isRunning(): self.tts_worker.stop(); self.tts_worker.wait(1000)
        if hasattr(self, "ai_worker") and self.ai_worker.isRunning(): self.ai_worker.requestInterruption(); self.ai_worker.wait(1000)
        if hasattr(self, "ollama_setup") and self.ollama_setup.isRunning(): self.ollama_setup.stop(); self.ollama_setup.wait(1000)
        if hasattr(self, "reminder_timer"): self.reminder_timer.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv); app.setApplicationName("Kadir's PDF App"); icon_path = APP_DIR / "kadir_pdf_logo.ico" if (APP_DIR / "kadir_pdf_logo.ico").exists() else APP_DIR / "kadir_pdf_logo.png"; app.setWindowIcon(QIcon(str(icon_path))); window = PDFStudio(); window.show(); sys.exit(app.exec())
