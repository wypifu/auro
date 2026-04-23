# auro

A simple AUR package manager for Arch Linux.

Manages AUR packages in a dedicated directory (`~/Builds/AUR` by default), with full control over sources — no magic, no hidden cache.

## Features

- 🔒 Security-first: PKGBUILD review before installation
- 📦 Dependency resolution: Automatic AUR dependency handling
- 🎨 Colored output: Clear visual feedback
- ⚡ Lightweight: Pure Python, minimal dependencies
- 🔧 Configurable: Multiple verification levels

## Dependencies

sudo pacman -S python-requests python-tomli-w

## Installation

sudo cp auro.py /usr/local/bin/auro
sudo chmod +x /usr/local/bin/auro

On first run, auro creates `~/.config/auro/config.toml` automatically.

## Configuration

# ~/.config/auro/config.toml
[auro]
aur_dir = "~/Builds/AUR"      # where package sources are cloned
verify_level = "normal"        # none | quick | normal | paranoid

### Verification Levels

| Level | Behavior |
|-------|----------|
| none | No PKGBUILD verification (dangerous) |
| quick | Show warnings only, no PKGBUILD review |
| normal | Show PKGBUILD and ask for confirmation (default) |
| paranoid | Full verification including source files |

## Usage

| Command | Description |
|---------|-------------|
| auro -S <package> | Install a package and its AUR dependencies |
| auro -Ss <query> | Search AUR for packages |
| auro -Syu | Update all AUR packages |
| auro -Syu <package> | Update a single package |
| auro -R <package> | Remove a package (offers to delete source) |
| auro -Q | List all packages managed by auro |
| auro -C | Clean orphaned source directories |

### Security Options

| Flag | Description |
|------|-------------|
| --noconfirm | Skip all confirmations (for scripts) |
| --no-verify | Skip PKGBUILD verification (not recommended) |
| --verify-level | Override config verification level |

## Examples

# Install with default verification (PKGBUILD review)
auro -S google-chrome

# Quick install with warnings only
auro -S yay --verify-level quick

# Update all packages without confirmations (script mode)
auro -Syu --noconfirm

# Search for packages
auro -Ss firefox

# Remove package and its source
auro -R telegram-desktop

## Security Features

When installing a package, auro automatically checks the PKGBUILD for:

- HTTP sources (not HTTPS) - integrity risk
- Missing checksums - file corruption risk
- curl | bash patterns - executes unverified code
- rm -rf / commands - system destruction risk
- Dynamic pkgver() - version may change on each build
- sudo in build functions - privilege escalation risk
- Installation into /home/ - potentially suspicious

Additionally, auro:
- Checks if base-devel is installed before building
- Detects if a package is already installed
- Prevents installing official repo packages (use pacman instead)
- Resolves AUR dependencies recursively

## Notes

- If a package is available in the official repos, auro will tell you to use pacman instead
- AUR dependencies are resolved recursively before installation
- Syu skips packages that are not installed (orphans). Use -C to clean them
- R proposes to delete the source directory (default: yes). If you decline, run -C later to batch clean
- Colors are automatically enabled when output goes to a terminal

## License

MIT
