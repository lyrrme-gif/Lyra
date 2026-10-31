# Lyra

**A Windows desktop assistant with a Dynamic Island-inspired interface.**

Lyra is a desktop assistant built around a compact, interactive island interface. It brings assistant features and desktop tools into one experience.

## ✨ Features

- **Dynamic Island-inspired interface** — a compact interface designed to stay accessible on the desktop.
- **Floating function launcher** — a visual launcher for accessing Lyra's available functions.
- **Activity interaction** — interact with the island to access the currently active feature.
- **Voice interaction** — multiple voice activation modes.
- **Music experience** — a dedicated interface for music playback and related information.
- **Customisation** — different visual themes and island presentation options.

*Some features may still be under development.*

## 🖥️ Screenshots

<!-- Add actual screenshots inside docs/screenshots/ -->

<!--
![Lyra main interface](docs/screenshots/main-interface.png)
![Lyra function launcher](docs/screenshots/function-launcher.png)
![Lyra music interface](docs/screenshots/music-interface.png)
-->

## 📁 Project Structure

```text
Lyra/
├── backend/       # Python backend
├── ui/            # Tauri desktop interface
├── README.md
└── .gitignore

Lyra-AI/           # Optional companion AI service
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
5. Open the extracted `Lyra` folder.

### Option 2 — Clone the repository

If Git is installed, open PowerShell and run:

```powershell
git clone https://github.com/lyrrme-gif/Lyra.git
cd Lyra
```

## 🧰 Requirements

Before running Lyra, install:

- [Python 3.13](https://www.python.org/downloads/)
- [Node.js (LTS)](https://nodejs.org/)
- [Rust](https://www.rust-lang.org/tools/install)
- [Git](https://git-scm.com/downloads/) (optional, for cloning)

Tauri development on Windows also requires the appropriate Microsoft C++ build tools and WebView2 runtime.

## 🚀 Installation and Running

Open PowerShell in the location where you extracted Lyra.

### 1. Set up the Python backend

Navigate to the backend folder:

```powershell
cd D:\App\Download\Lyra\backend
```

Create a Python virtual environment:

```powershell
py -3.13 -m venv venv
```

Install the required dependencies:

```powershell
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install -r requirements-optional.txt
```

Start the backend:

```powershell
venv\Scripts\python main.py
```

Alternatively, if the virtual environment is already created:

```powershell
cd D:\App\Download\Lyra\backend
.\venv\Scripts\activate
python main.py
```

### 2. Install and run the desktop interface

Open a **second PowerShell window**:

```powershell
cd D:\App\Download\Lyra\ui
```

Install the UI dependencies:

```powershell
npm install
```

Start Lyra in development mode:

```powershell
npm run tauri dev
```

If your `package.json` defines the `tauri:dev` script instead, use:

```powershell
npm run tauri:dev
```

Keep the backend running while using the desktop application.

### 3. Optional — Run Lyra-AI

If you have the separate `Lyra-AI` component installed, open another PowerShell window:

```powershell
cd D:\App\Download\Lyra-AI
python main.py
```

## 🏗️ Building Lyra

To create a production build:

### 1. Prepare the backend

Make sure the backend dependencies are installed:

```powershell
cd D:\App\Download\Lyra\backend

py -3.13 -m venv venv

venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install -r requirements-optional.txt
```

### 2. Install UI dependencies

```powershell
cd D:\App\Download\Lyra\ui

npm install
```

### 3. Build the desktop application

```powershell
npm run tauri build
```

The generated installer and application files will be placed in the Tauri build output directory, usually under:

```text
ui/src-tauri/target/release/bundle/
```

*Note: The backend packaging and installer integration may require additional build steps depending on the project's current build configuration.*

## 🔐 Configuration and API Keys

Some Lyra features may require external service credentials or API keys.

- Configure your own API keys locally.
- Never upload API keys, passwords, or private tokens to GitHub.
- Keep `.env` files and other private configuration out of the repository.
- Use an `.env.example` file to document required environment variables without including actual secrets.

## 🛠️ Development Status

Lyra is actively developed. Features, interface details, and internal architecture may change as development continues.

## 📦 Releases and Downloads

For published versions and ready-to-install Windows builds, visit:

[**Lyra Releases**](https://github.com/lyrrme-gif/Lyra/releases)

## 🐛 Bug Reports and Contributions

If you encounter a bug or have a suggestion, you can open an issue in the GitHub repository.

## 📄 License

No license has been specified yet.

Until a license is added, do not assume that others have permission to redistribute or modify this project.
