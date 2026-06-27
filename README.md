# 🔍 Know Your Projects

A desktop app for **AI-powered code exploration**. Load any local or GitHub project and instantly understand its structure, files, functions, and dependencies — powered by the Gemini API.

---

## ✨ Features

- 📂 Open local folders or GitHub repos
- 💻 Syntax-highlighted code viewer with clickable functions
- 🤖 AI explanations for projects, files, and functions (Gemini API)
- 🕸 Interactive dependency graph (file & function call graphs)
- 🪟 Full-screen dependency explorer with search, filters & node details
- 🗺 Mind map view of project structure
- 🎨 Dark & Light themes

---

## 🛠 Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```
Or on Windows, double-click `install_deps.bat`.

### 2. Get a Gemini API key
Get a free key at [aistudio.google.com](https://aistudio.google.com)

### 3. Run the app
```bash
python main.py
```
Or double-click `run_app.bat` on Windows.

### 4. Add your API key
Go to **Settings (⚙️) → Gemini API Key**, paste your key and click Save.

---

## 📁 Project Structure

```
├── main.py                  # Entry point
├── requirements.txt
├── core/
│   ├── ai_explainer.py      # Gemini API wrapper
│   ├── code_parser.py       # Multi-language code parser
│   ├── dependency_analyzer.py # Call/import graph builder
│   ├── file_tree_builder.py
│   ├── project_loader.py
│   └── cache_manager.py
├── ui/
│   ├── main_window.py       # Root window
│   ├── code_viewer.py
│   ├── explanation_panel.py
│   ├── dependency_graph_panel.py
│   ├── dependency_window.py # Full-screen graph window
│   ├── file_tree_panel.py
│   ├── function_popup.py
│   ├── mindmap_widget.py
│   ├── settings_dialog.py
│   └── welcome_screen.py
└── assets/styles/
    ├── dark_theme.qss
    └── light_theme.qss
```

---

## 🔧 Requirements

- Python 3.11+
- PyQt6
- google-genai
- networkx
- pyvis

---

## 📄 License

MIT
