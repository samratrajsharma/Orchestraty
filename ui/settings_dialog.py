"""
settings_dialog.py — Settings panel for API key, theme, and cache management.
"""
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QFormLayout, QComboBox,
    QSpinBox, QCheckBox, QMessageBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtGui import QFont


class SettingsDialog(QDialog):
    """Application settings dialog."""

    theme_changed = pyqtSignal(str)
    api_key_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        
        super().__init__(parent)
        self.setWindowTitle("Settings — Know Your Projects")
        self.setMinimumWidth(520)
        self.setMinimumHeight(400)
        self.settings = QSettings("KYP", "KnowYourProjects")
        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title
        title = QLabel("⚙️ Settings")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        # ── API Key Section ──────────────────────────────────────────────
        api_group = QGroupBox("🤖 AI Configuration")
        api_layout = QFormLayout(api_group)
        api_layout.setSpacing(10)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("Enter your Gemini API key...")
        self.api_key_input.setMinimumHeight(36)

        self.show_key_btn = QPushButton("👁 Show")
        self.show_key_btn.setFixedWidth(70)
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)

        self.test_key_btn = QPushButton("🔍 Test Key")
        self.test_key_btn.setFixedWidth(90)
        self.test_key_btn.clicked.connect(self._test_api_key)

        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_input)
        key_row.addWidget(self.show_key_btn)
        key_row.addWidget(self.test_key_btn)

        api_layout.addRow("Gemini API Key:", key_row)

        self.model_combo = QComboBox()
        self.model_combo.addItems([
            "gemini-2.0-flash-lite",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
            "gemini-1.5-pro",
        ])
        api_layout.addRow("Model:", self.model_combo)

        get_key_label = QLabel(
            '🔗 Get a free API key at <a href="https://aistudio.google.com" '
            'style="color:#a78bfa;">aistudio.google.com</a>'
        )
        get_key_label.setOpenExternalLinks(True)
        get_key_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        api_layout.addRow("", get_key_label)

        layout.addWidget(api_group)

        # ── Appearance Section ───────────────────────────────────────────
        appearance_group = QGroupBox("🎨 Appearance")
        appearance_layout = QFormLayout(appearance_group)
        appearance_layout.setSpacing(10)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark (Default)", "Light"])
        appearance_layout.addRow("Theme:", self.theme_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 20)
        self.font_size_spin.setValue(10)
        appearance_layout.addRow("Font Size:", self.font_size_spin)

        layout.addWidget(appearance_group)

        # ── Analysis Section ─────────────────────────────────────────────
        analysis_group = QGroupBox("🔍 Analysis")
        analysis_layout = QFormLayout(analysis_group)
        analysis_layout.setSpacing(10)

        self.max_files_spin = QSpinBox()
        self.max_files_spin.setRange(100, 5000)
        self.max_files_spin.setValue(2000)
        self.max_files_spin.setSuffix(" files")
        analysis_layout.addRow("Max Files:", self.max_files_spin)

        self.cache_enabled = QCheckBox("Enable local caching (faster re-opens)")
        self.cache_enabled.setChecked(True)
        analysis_layout.addRow("", self.cache_enabled)

        self.clear_cache_btn = QPushButton("🗑 Clear Cache")
        self.clear_cache_btn.clicked.connect(self._clear_cache)
        analysis_layout.addRow("Cache:", self.clear_cache_btn)

        layout.addWidget(analysis_group)

        # ── Buttons ──────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_key_visibility(self, checked: bool):
        if checked:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_key_btn.setText("🙈 Hide")
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_key_btn.setText("👁 Show")

    def _test_api_key(self):
        from core.ai_explainer import AIExplainer
        api_key = self.api_key_input.text().strip()
        if not api_key:
            QMessageBox.warning(self, "No API Key", "Please enter an API key first.")
            return
        self.test_key_btn.setText("Testing...")
        self.test_key_btn.setEnabled(False)
        explainer = AIExplainer(api_key)
        ok, msg = explainer.test_api_key(api_key)
        self.test_key_btn.setText("🔍 Test Key")
        self.test_key_btn.setEnabled(True)
        if ok:
            QMessageBox.information(self, "API Key Valid", msg)
        else:
            QMessageBox.critical(self, "API Key Invalid", msg)

    def _clear_cache(self):
        reply = QMessageBox.question(
            self, "Clear Cache",
            "This will delete all cached analysis data. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            QMessageBox.information(self, "Cache Cleared",
                                    "Cache has been cleared. Next project open will re-analyze.")

    def _load_settings(self):
        self.api_key_input.setText(self.settings.value("api_key", ""))
        self.model_combo.setCurrentText(
            self.settings.value("model", "gemini-2.0-flash"))
        theme = self.settings.value("theme", "Dark (Default)")
        idx = self.theme_combo.findText(theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.max_files_spin.setValue(int(self.settings.value("max_files", 2000)))
        self.cache_enabled.setChecked(
            self.settings.value("cache_enabled", True, type=bool))

    def _save_settings(self):
        self.settings.setValue("api_key", self.api_key_input.text().strip())
        self.settings.setValue("model", self.model_combo.currentText())
        self.settings.setValue("theme", self.theme_combo.currentText())
        self.settings.setValue("max_files", self.max_files_spin.value())
        self.settings.setValue("cache_enabled", self.cache_enabled.isChecked())
        self.api_key_changed.emit(self.api_key_input.text().strip())
        self.theme_changed.emit(self.theme_combo.currentText())
        self.accept()

    @staticmethod
    def get_api_key() -> str:
        settings = QSettings("KYP", "KnowYourProjects")
        return settings.value("api_key", "")

    @staticmethod
    def get_model() -> str:
        settings = QSettings("KYP", "KnowYourProjects")
        return settings.value("model", "gemini-2.0-flash-lite")

    @staticmethod
    def get_max_files() -> int:
        settings = QSettings("KYP", "KnowYourProjects")
        return int(settings.value("max_files", 2000))

    @staticmethod
    def is_cache_enabled() -> bool:
        settings = QSettings("KYP", "KnowYourProjects")
        return settings.value("cache_enabled", True, type=bool)
