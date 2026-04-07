# HyprVault

<p align="center">
  <img src="https://img.shields.io/badge/Hyprland-Session%20Manager-blue?style=for-the-badge&logo=linux&logoColor=white" alt="HyprVault"/>
</p>

**HyprVault** is a lightweight session manager for [Hyprland](https://hyprland.org/). Save your window layout, workspaces, and positions — restore them anytime.

## ✨ Features

- 💾 **Save Sessions** — Capture window positions, workspaces, floating/tiled state, and fullscreen mode
- 🔄 **Restore Sessions** — Reopen windows in their exact positions with proper workspace assignment
- 📋 **List Sessions** — View all saved sessions
- 🗑️ **Delete Sessions** — Remove unwanted session files
- 🏠 **XDG Compliant** — Sessions stored in `~/.config/hyprvault/sessions/`

## 📦 Installation

### From AUR (Arch Linux)

```bash
yay -S hyprvault
```

### From Source

```bash
git clone https://github.com/Tunahanyrd/hyprvault.git
cd hyprvault
pip install .
```

#### Shell Completions (optional)

AUR package installs these automatically. For pip users:

```bash
# Fish
cp completions/hyprvault.fish ~/.config/fish/completions/

# Bash
cp completions/hyprvault.bash ~/.local/share/bash-completion/completions/hyprvault

# Zsh
cp completions/_hyprvault ~/.local/share/zsh/site-functions/
```

## 🚀 Usage

```bash
# Save current session
hyprvault save my_workspace

# Restore a session
hyprvault load my_workspace

# List all saved sessions
hyprvault list

# Delete a session
hyprvault delete my_workspace

# Show help
hyprvault help
```

## 📁 Session Storage

Sessions are saved as JSON files in:
```
~/.config/hyprvault/sessions/
```

Each session captures:
- Window class name and command
- Workspace ID
- Floating/tiled state
- Window position and size
- Fullscreen state
- Focus history

## ⚙️ How It Works

1. **Save**: Uses `hyprctl clients -j` to get window info, reads `/proc/{pid}/cmdline` for commands
2. **Load**: Matches existing windows by class name, moves them to saved workspaces, or spawns new ones

## 🧪 Restore Tracing

For debugging tricky restore issues, you can enable a detailed action trace for a single run:

```bash
HYPRVAULT_TRACE_ACTIONS=1 hyprvault load my_workspace
```

Optional custom trace path:

```bash
HYPRVAULT_TRACE_ACTIONS=1 HYPRVAULT_TRACE_PATH=/tmp/hyprvault-trace.log hyprvault load my_workspace
```

The trace records workspace switches, focus attempts, spawn attempts, deferred restores, and final focus decisions.
Default trace path:

```bash
/tmp/hyprvault-action-trace.log
```

## 🤝 Contributing

Contributions welcome! Feel free to open issues or PRs.

## 📄 License

MIT License — See [LICENSE](LICENSE) for details.
