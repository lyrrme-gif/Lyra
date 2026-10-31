# Lyra

**A Windows desktop assistant with a Dynamic Island-inspired interface.**

Lyra is a desktop assistant designed around a compact, interactive island interface. It brings assistant features and desktop tools together in one customizable desktop experience.

## ✨ Features

- **Dynamic Island-inspired interface** — a compact interface designed to stay accessible on your desktop.
- **Floating function launcher** — a visual constellation launcher for accessing Lyra's available functions.
- **Activity interaction** — interact with the island to access the currently active feature.
- **Voice interaction** — multiple voice activation modes.
- **Music experience** — a dedicated interface for music playback and related information.
- **Customisation** — different visual themes and island presentation options.
- **Search experience** — integrated search interfaces for supported services.

*Features and availability may vary depending on the current version.*

## 🖥️ Screenshots

<!-- Add actual Lyra screenshots inside docs/screenshots/ -->

<!--
![Lyra main interface](docs/screenshots/main-interface.png)

![Lyra function launcher](docs/screenshots/function-launcher.png)

![Lyra music interface](docs/screenshots/music-interface.png)

![Lyra search interface](docs/screenshots/search-interface.png)
-->

## 📁 Project Structure

```text
Lyra/
│
├── backend/                 # Python backend
│   ├── main.py
│   ├── requirements.txt
│   └── requirements-optional.txt
│
├── ui/                      # Tauri desktop interface
│   ├── package.json
│   └── src-tauri/
│
├── README.md
└── .gitignore

Lyra-AI/                     # Optional companion AI component
```

## ⚙️ Technologies

- Python 3.13
- JavaScript / TypeScript
- Node.js and npm
- Tauri
- Rust

## 📥 Downloading Lyra

### Option 1 — Download ZIP

1. Open the [Lyra GitHub repository](https://github.com/lyrrme-gif/Lyra).
2. Click the green **Code** button.
3. Select **Download ZIP**.
4. Extract the downloaded ZIP file.
5. Open the extracted project folder.

### Option 2 — Clone the repository

If Git is installed, open PowerShell in the directory where you want Lyra to be installed:

```powershell
git clone https://github.com/lyrrme-gif/Lyra.git
```

Then open the project:

```powershell
cd Lyra
```

## 🧰 Requirements

Before running Lyra, install:

- [Python 3.13](https://www.python.org/downloads/)
- [Node.js (LTS)](https://nodejs.org/)
- [Rust](https://www.rust-lang.org/tools/install)
- [Git](https://git-scm.com/downloads/) (optional, for cloning)

For Tauri development on Windows, you also need the Microsoft C++ Build Tools and WebView2 runtime.

## 🚀 Installation and Running

Open PowerShell in the directory containing the extracted `Lyra` folder.

The following commands use relative paths, so you do not need to change them to match your personal Windows username or download directory.

### 1. Set up the Python backend

Navigate to the backend directory:

```powershell
cd Lyra\backend
```

Create a Python virtual environment:

```powershell
py -3.13 -m venv venv
```

Install the required dependencies:

```powershell
venv\Scripts\pip install -r requirements.txt
```

Install optional dependencies:

```powershell
venv\Scripts\pip install -r requirements-optional.txt
```

Start the backend:

```powershell
venv\Scripts\python main.py
```

Keep this terminal open while using Lyra.

### 2. Install and run the desktop interface

Open a **second PowerShell window**.

Navigate to the UI directory:

```powershell
cd Lyra\ui
```

Install the UI dependencies:

```powershell
npm install
```

Start Lyra in development mode:

```powershell
npm run tauri dev
```

Keep both the backend and UI running while using the application.

### 3. Optional — Run Lyra-AI

If you also have the separate `Lyra-AI` component, open another PowerShell window.

Navigate to its directory:

```powershell
cd Lyra-AI
```

Start it:

```powershell
python main.py
```

The `Lyra-AI` folder should be located alongside the `Lyra` folder for this relative path to work.

## 🏗️ Building Lyra

To build the desktop application for production, follow these steps.

### 1. Prepare the backend

Open PowerShell in the directory containing `Lyra`:

```powershell
cd Lyra\backend
```

Create the virtual environment:

```powershell
py -3.13 -m venv venv
```

Install the dependencies:

```powershell
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install -r requirements-optional.txt
```

### 2. Install UI dependencies

Navigate to the UI directory:

```powershell
cd ..\ui
```

Install dependencies:

```powershell
npm install
```

### 3. Build the Tauri application

Run:

```powershell
npm run tauri build
```

After a successful build, generated application bundles are usually located under:

```text
ui/src-tauri/target/release/bundle/
```

**Important:** The Tauri build command builds the desktop application. If your release installer also needs the Python backend packaged and included, run the project's backend packaging and installer-staging process as well. The exact steps depend on the current build configuration.

## 🔐 Configuration and API Keys

Some Lyra features may require external service credentials or API keys.

- Configure your own API keys locally.
- Never upload API keys, passwords, or private tokens to GitHub.
- Keep `.env` files and private configuration out of the repository.
- Use an `.env.example` file to document required environment variables without exposing actual credentials.

## 🛠️ Development Status

Lyra is actively developed. Features, interface details, and internal architecture may change as development continues.

## 📦 Releases and Downloads

For published versions and ready-to-install Windows builds, visit:

**[Lyra Releases](https://github.com/lyrrme-gif/Lyra/releases)**

## 🐛 Bug Reports and Suggestions

Found a bug or have an idea for improvement?

You can report issues and share suggestions through the GitHub repository's Issues section.

## 📄 License

No license has been specified yet.

Until a license is added, do not assume that others have permission to redistribute or modify this project.
