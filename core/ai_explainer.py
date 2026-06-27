"""
ai_explainer.py — Gemini API wrapper for AI-powered code explanations.
Supports streaming (typewriter effect), caching, and multiple explanation types.
Uses the new google.genai SDK (replaces deprecated google.generativeai).
"""
import os
from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool


class ExplainWorker(QRunnable):
    """Worker that runs AI explanation in a thread pool."""

    class Signals(QObject):
        chunk = pyqtSignal(str)
        finished = pyqtSignal(str)
        error = pyqtSignal(str)

    def __init__(self, prompt: str, api_key: str, model_name: str = "gemini-2.0-flash-lite"):
        super().__init__()
        self.prompt = prompt
        self.api_key = api_key
        self.model_name = model_name
        self.signals = ExplainWorker.Signals()

    def run(self):
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)

            full_text = ""
            for chunk in client.models.generate_content_stream(
                model=self.model_name,
                contents=self.prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=1024,
                ),
            ):
                if chunk.text:
                    full_text += chunk.text
                    self.signals.chunk.emit(chunk.text)

            self.signals.finished.emit(full_text)

        except Exception as e:
            self.signals.error.emit(str(e))


class AIExplainer(QObject):
    """
    Manages all Gemini API interactions for code explanation.
    Supports streaming output for typewriter effect in the UI.
    """

    explanation_chunk = pyqtSignal(str)    # Partial text as it streams
    explanation_done = pyqtSignal(str)     # Full explanation when complete
    explanation_error = pyqtSignal(str)    # Error message

    PROJECT_PROMPT = """You are an expert software engineer helping a developer understand an unfamiliar codebase.

Analyze this project structure and provide a clear, concise overview in plain English.

Project Name: {project_name}
Primary Language: {language}
Total Files: {total_files}
File Structure:
{file_structure}

Please explain:
1. **What this project does** (in 2-3 sentences)
2. **How it's organized** (key folders and their purpose)
3. **Main entry points** (where execution begins)
4. **Key technologies/frameworks used**
5. **A quick guide** for a new developer to understand it

Keep explanations beginner-friendly but technically accurate. Use markdown formatting."""

    FILE_PROMPT = """You are an expert software engineer. Explain this source file clearly and concisely.

File: {filename}
Language: {language}
Lines of code: {line_count}

```{language_lower}
{code_snippet}
```

Please explain:
1. **Purpose** — What does this file do?
2. **Key components** — List the main functions/classes and what each does (one line each)
3. **Dependencies** — What does this file import/depend on?
4. **Role in project** — How does this fit into the bigger picture?

Be concise. Use bullet points. Developer-friendly tone."""

    FUNCTION_PROMPT = """You are an expert software engineer. Explain this function clearly.

Function: `{func_name}` in `{filename}`
Language: {language}

```{language_lower}
{func_code}
```

Please explain:
1. **What it does** — One clear sentence
2. **Parameters** — What each input means
3. **Returns** — What it gives back
4. **How it works** — Step-by-step logic (3-5 bullet points)
5. **Edge cases** — Any important conditions or gotchas

Keep it concise and developer-friendly."""

    def __init__(self, api_key: str = "", parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._cache: dict = {}
        self._thread_pool = QThreadPool.globalInstance()

    def set_api_key(self, api_key: str):
        self._api_key = api_key

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key and len(self._api_key) > 10)

    def explain_project(self, project_name: str, language: str,
                        total_files: int, file_structure: str):
        """Generate project overview explanation."""
        cache_key = f"project::{project_name}"
        if cache_key in self._cache:
            self.explanation_chunk.emit(self._cache[cache_key])
            self.explanation_done.emit(self._cache[cache_key])
            return

        prompt = self.PROJECT_PROMPT.format(
            project_name=project_name,
            language=language,
            total_files=total_files,
            file_structure=file_structure,
        )
        self._run_async(prompt, cache_key)

    def explain_file(self, file_path: str, language: str,
                     line_count: int, code_snippet: str):
        """Generate file-level explanation."""
        filename = os.path.basename(file_path)
        cache_key = f"file::{file_path}"
        if cache_key in self._cache:
            self.explanation_chunk.emit(self._cache[cache_key])
            self.explanation_done.emit(self._cache[cache_key])
            return

        # Limit code snippet length
        lines = code_snippet.split("\n")[:80]
        snippet = "\n".join(lines)
        if len(lines) == 80:
            snippet += "\n... (truncated)"

        prompt = self.FILE_PROMPT.format(
            filename=filename,
            language=language,
            line_count=line_count,
            language_lower=language.lower().replace(" ", ""),
            code_snippet=snippet,
        )
        self._run_async(prompt, cache_key)

    def explain_function(self, func_name: str, file_path: str,
                         language: str, func_code: str):
        """Generate function-level explanation."""
        filename = os.path.basename(file_path)
        cache_key = f"func::{file_path}::{func_name}"
        if cache_key in self._cache:
            self.explanation_chunk.emit(self._cache[cache_key])
            self.explanation_done.emit(self._cache[cache_key])
            return

        prompt = self.FUNCTION_PROMPT.format(
            func_name=func_name,
            filename=filename,
            language=language,
            language_lower=language.lower().replace(" ", ""),
            func_code=func_code[:1500],
        )
        self._run_async(prompt, cache_key)

    def _run_async(self, prompt: str, cache_key: str):
        """Run explanation in background thread."""
        if not self.has_api_key:
            self.explanation_error.emit(
                "⚠️ No Gemini API key configured.\n\n"
                "Please add your API key in Settings → API Key.\n"
                "Get a free key at: https://aistudio.google.com"
            )
            return

        worker = ExplainWorker(prompt, self._api_key)
        worker.signals.chunk.connect(self.explanation_chunk)
        worker.signals.error.connect(self.explanation_error)

        def on_done(full_text):
            self._cache[cache_key] = full_text
            self.explanation_done.emit(full_text)

        worker.signals.finished.connect(on_done)
        self._thread_pool.start(worker)

    def clear_cache(self):
        """Clear all cached explanations."""
        self._cache.clear()

    def test_api_key(self, api_key: str) -> tuple[bool, str]:
        """Synchronously test if an API key is valid."""
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents="Reply with only: OK",
            )
            return True, "API key is valid! ✅"
        except Exception as e:
            return False, f"API key error: {str(e)}"
