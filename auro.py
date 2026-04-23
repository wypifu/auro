#!/usr/bin/env python3
"""
auro - A simple AUR package manager
Usage:
    auro -S <package>       Install a package (and its AUR dependencies)
    auro -Ss <query>        Search AUR for packages
    auro -Syu [package]     Update one or all AUR packages
    auro -R <package>       Remove a package (offers to delete source dir)
    auro -C                 Clean orphaned source directories
"""

import sys
import shutil
import tomllib
import tomli_w
import argparse
import requests
import subprocess
import re
import os
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".config" / "auro" / "config.toml"
AUR_RPC     = "https://aur.archlinux.org/rpc/v5"
AUR_GIT     = "https://aur.archlinux.org/{}.git"

# ─── Colors ───────────────────────────────────────────────────────────────────

R     = "\033[0m"
BOLD  = "\033[1m"
GREEN = "\033[32m"
CYAN  = "\033[36m"
YELLW = "\033[33m"
RED   = "\033[31m"
GRAY  = "\033[90m"

def info(msg):  print(f"{BOLD}[auro]{R} {msg}")
def ok(msg):    print(f"{BOLD}[auro]{R} {GREEN}{msg}{R}")
def warn(msg):  print(f"{BOLD}[auro]{R} {YELLW}{msg}{R}")
def err(msg):   print(f"{BOLD}[auro]{R} {RED}{msg}{R}")


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        create_default_config()
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def create_default_config():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    default = {
        "auro": {
            "aur_dir": str(Path.home() / "Builds" / "AUR"),
            "verify_level": "normal"  # none | quick | normal | paranoid
        }
    }
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(default, f)
    info(f"Config created at {CONFIG_PATH}")
    info(f"AUR directory set to: {default['auro']['aur_dir']}")
    info(f"Verify level set to: {default['auro']['verify_level']}")
    info(f"Edit {CONFIG_PATH} to customize.")


def get_aur_dir(config: dict) -> Path:
    path = Path(config["auro"]["aur_dir"]).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_verify_level(config: dict, cli_no_verify: bool = False, cli_verify_level: str = None) -> str:
    """Determine verification level from config and CLI flags."""
    if cli_no_verify:
        return "none"
    if cli_verify_level:
        return cli_verify_level
    return config.get("auro", {}).get("verify_level", "normal")


def get_verify_level_display(level: str) -> str:
    """Return a colored display string for verify level."""
    colors = {
        "none": f"{RED}none{R} (⚠️  no verification)",
        "quick": f"{YELLW}quick{R} (warnings only)",
        "normal": f"{GREEN}normal{R} (PKGBUILD review)",
        "paranoid": f"{CYAN}paranoid{R} (full verification)"
    }
    return colors.get(level, level)


# ─── AUR API ──────────────────────────────────────────────────────────────────

def aur_info(package: str) -> dict | None:
    try:
        r = requests.get(f"{AUR_RPC}/info", params={"arg": package}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data["resultcount"] == 0:
            return None
        return data["results"][0]
    except requests.RequestException as e:
        err(f"AUR API error: {e}")
        return None


def is_aur_package(package: str) -> bool:
    return aur_info(package) is not None


def is_pacman_package(package: str) -> bool:
    result = subprocess.run(["pacman", "-Si", package], capture_output=True)
    return result.returncode == 0


def is_installed(package: str) -> bool:
    result = subprocess.run(["pacman", "-Q", package], capture_output=True)
    return result.returncode == 0


def installed_version(package: str) -> str | None:
    result = subprocess.run(["pacman", "-Q", package], capture_output=True, text=True)
    if result.returncode == 0:
        parts = result.stdout.strip().split()
        return parts[-1] if len(parts) >= 2 else None
    return None


def is_base_devel_installed() -> tuple[bool, list[str]]:
    """Check if makepkg dependencies are available. Returns (is_installed, [missing_packages])."""

    # First, try to run makepkg --version to see if it works
    result = subprocess.run(["makepkg", "--version"], capture_output=True)
    if result.returncode == 0:
        return True, []  # makepkg works, we're good

    # If not, identify what's missing
    essential = {'gcc', 'make', 'autoconf', 'automake', 'patch', 'binutils', 'pkgconf'}
    missing = []
    for pkg in essential:
        result = subprocess.run(["pacman", "-Q", pkg], capture_output=True)
        if result.returncode != 0:
            missing.append(pkg)

    return len(missing) == 0, missing


def ensure_base_devel(noconfirm: bool = False) -> bool:
    """
    Check if base-devel is installed. If not, propose to install it.
    Returns True if base-devel is available (either already installed or user installed it).
    """
    base_devel_ok, missing = is_base_devel_installed()

    if base_devel_ok:
        return True

    # Not installed - show warning and propose installation
    warn("base-devel is not fully installed. makepkg will fail without it.")
    if missing:
        print(f"  {GRAY}Missing essential packages: {', '.join(missing)}{R}")
    print(f"  {GRAY}base-devel contains gcc, make, and other build tools needed to compile from source.{R}")

    if noconfirm:
        warn("Skipping base-devel installation due to --noconfirm. Build will likely fail.")
        return False

    print()
    confirm = input(f"{BOLD}Install base-devel now?{R} [Y/n]: ").strip().lower()

    if confirm in ("", "y", "yes"):
        info("Installing base-devel...")
        result = subprocess.run(["sudo", "pacman", "-S", "--needed", "base-devel"])
        if result.returncode == 0:
            ok("base-devel installed successfully.")
            return True
        else:
            err("Failed to install base-devel.")
            return False
    else:
        print()
        warn("Proceeding without base-devel. This will likely cause makepkg to fail.")
        warn("If compilation fails, install base-devel with: sudo pacman -S --needed base-devel")
        print()
        confirm2 = input(f"{BOLD}Continue anyway?{R} [y/N]: ").strip().lower()
        return confirm2 in ("y", "yes")


# ─── Security Verification ──────────────────────────────────────────────────────

def auto_verify(pkg_dir: Path) -> tuple[bool, list[str]]:
    """
    Automatic security checks on PKGBUILD.
    Returns (is_safe, list_of_warnings)
    """
    warnings = []
    pkgbuild = pkg_dir / "PKGBUILD"

    if not pkgbuild.exists():
        return False, ["PKGBUILD not found"]

    content = pkgbuild.read_text()

    # Check 1: HTTP vs HTTPS in source
    http_sources = re.findall(r'source=.*http://', content)
    if http_sources:
        warnings.append("⚠️  HTTP source detected (not HTTPS) - integrity risk")

    # Check 2: Missing checksums
    if 'sha256sums' not in content and 'sha512sums' not in content and \
       'b2sums' not in content and 'md5sums' not in content:
        warnings.append("⚠️  No checksums (sha256/md5/b2) - file corruption risk")

    # Check 3: Installing files into /home/
    if re.search(r"['\"]/home/", content):
        warnings.append("⚠️  Installation into /home/ - potentially suspicious")

    # Check 4: curl | bash pattern
    if 'curl' in content and '| bash' in content:
        warnings.append("🔴 DANGER: curl | bash detected - executes unverified code")

    # Check 5: rm -rf / patterns
    if 'rm -rf /' in content or 'rm -rf /*' in content:
        warnings.append("🔴 CRITICAL: rm -rf / detected - system destruction risk")

    # Check 6: Dynamic pkgver()
    if 'pkgver()' in content:
        warnings.append("ℹ️  Dynamic pkgver() - version may change on each build")

    # Check 7: Running commands as root in build()
    if 'sudo' in content and 'build()' in content:
        warnings.append("⚠️  sudo command in build() - privilege escalation risk")

    return True, warnings


def interactive_review(pkg_dir: Path, package: str, warnings: list[str], verify_level: str) -> bool:
    """
    Ask user to review package before installation.
    Returns True if user approves installation.
    """
    if verify_level == "none":
        return True

    if verify_level == "quick":
        # Only show warnings, no PKGBUILD review
        if warnings:
            print(f"\n{BOLD}{YELLW}Warnings for {package}:{R}")
            for w in warnings:
                print(f"  {w}")
            print()
            confirm = input(f"{BOLD}Continue with installation?{R} [y/N]: ")
            return confirm.lower() in ('y', 'yes')
        return True

    # Normal or paranoid mode
    print(f"\n{BOLD}{CYAN}━━━ Package Review: {package} ━━━{R}\n")

    # Show auto-warnings
    if warnings:
        print(f"{BOLD}{YELLW}Automatic warnings:{R}")
        for w in warnings:
            print(f"  {w}")
        print()

    # Main review menu
    while True:
        print(f"{BOLD}What would you like to do?{R}")
        print(f"  {GREEN}[1]{R} View PKGBUILD (recommended)")

        install_file = pkg_dir / f"{package}.install"
        if install_file.exists():
            print(f"  {GREEN}[2]{R} View .install script")

        srcinfo = pkg_dir / ".SRCINFO"
        if srcinfo.exists():
            print(f"  {GREEN}[3]{R} View .SRCINFO")

        if verify_level == "paranoid":
            print(f"  {GREEN}[4]{R} Verify source files (makepkg --verifysource)")

        print(f"  {GREEN}[c]{R} Continue installation without viewing")
        print(f"  {RED}[a]{R} Abort installation")

        choice = input(f"\n{BOLD}Your choice{R} [1/c/a]: ").strip().lower()

        if choice == "1":
            subprocess.run(["less", str(pkg_dir / "PKGBUILD")])
            # After viewing, ask for final confirmation
            confirm = input(f"\n{BOLD}Install {package} after review?{R} [y/N]: ")
            return confirm.lower() in ('y', 'yes')

        elif choice == "2" and install_file.exists():
            subprocess.run(["less", str(install_file)])
            # Loop back to menu

        elif choice == "3" and srcinfo.exists():
            subprocess.run(["less", str(srcinfo)])

        elif choice == "4" and verify_level == "paranoid":
            info("Verifying source files...")
            subprocess.run(["makepkg", "--verifysource"], cwd=pkg_dir)
            confirm = input(f"\n{BOLD}Source verification complete. Install?{R} [y/N]: ")
            return confirm.lower() in ('y', 'yes')

        elif choice == "c":
            warn(f"Installing {package} without visual PKGBUILD review")
            confirm = input(f"Confirm anyway? [y/N]: ")
            return confirm.lower() in ('y', 'yes')

        elif choice == "a":
            return False

        else:
            warn(f"Invalid choice: {choice}")


def review_update(pkg_dir: Path, package: str, old_version: str, new_version: str) -> bool:
    """For updates, show diff if possible and ask for confirmation."""
    print(f"\n{BOLD}{CYAN}━━━ Update: {package} ━━━{R}")
    print(f"  {YELLW}{old_version}{R} → {GREEN}{new_version}{R}\n")

    # Try to show diff of PKGBUILD
    old_pkgbuild = pkg_dir / "PKGBUILD"
    backup = pkg_dir / "PKGBUILD.old"

    if backup.exists():
        print(f"{BOLD}Changes in PKGBUILD:{R}")
        result = subprocess.run(["diff", "-u", str(backup), str(old_pkgbuild)],
                                capture_output=True, text=True)
        if result.stdout:
            # Use less for paging if output is long
            if result.stdout.count('\n') > 20:
                subprocess.run(["less"], input=result.stdout, text=True)
            else:
                print(result.stdout)
        else:
            info("No changes in PKGBUILD")
        print()

    confirm = input(f"{BOLD}Apply this update?{R} [y/N]: ")
    return confirm.lower() in ('y', 'yes')


# ─── Dependency Resolver ──────────────────────────────────────────────────────

def resolve_aur_deps(package: str, visited: set = None) -> list[str]:
    if visited is None:
        visited = set()
    if package in visited:
        return []
    visited.add(package)

    pkg_info = aur_info(package)
    if pkg_info is None:
        return []

    all_deps = (pkg_info.get("MakeDepends") or []) + (pkg_info.get("Depends") or [])

    def strip_version(dep: str) -> str:
        for op in [">=", "<=", "!=", "=", ">", "<"]:
            if op in dep:
                return dep.split(op)[0]
        return dep

    aur_deps = []
    for dep in all_deps:
        dep_name = strip_version(dep)
        if is_installed(dep_name) or is_pacman_package(dep_name):
            continue
        if is_aur_package(dep_name):
            aur_deps.append(dep_name)

    ordered = []
    for dep in aur_deps:
        ordered += resolve_aur_deps(dep, visited)
        if dep not in ordered:
            ordered.append(dep)
    return ordered


# ─── Install ──────────────────────────────────────────────────────────────────

def clone_and_build(package: str, aur_dir: Path, noconfirm: bool = False,
                    verify_level: str = "normal", is_update: bool = False,
                    old_version: str = None) -> bool:
    """
    Clone (or update) and build a package.
    Returns True on success.
    """
    pkg_dir = aur_dir / package
    backup_pkgbuild = pkg_dir / "PKGBUILD.old" if is_update else None

    # Backup old PKGBUILD before updating
    if is_update and backup_pkgbuild and (pkg_dir / "PKGBUILD").exists():
        shutil.copy(pkg_dir / "PKGBUILD", backup_pkgbuild)

    # Clone or pull
    if pkg_dir.exists():
        info(f"Updating existing clone: {CYAN}{pkg_dir}{R}")
        result = subprocess.run(["git", "pull"], cwd=pkg_dir)
        if result.returncode != 0:
            err(f"git pull failed for '{package}'")
            return False
    else:
        url = AUR_GIT.format(package)
        info(f"Cloning {CYAN}{url}{R} → {pkg_dir}")
        result = subprocess.run(["git", "clone", url, str(pkg_dir)])
        if result.returncode != 0:
            err(f"git clone failed for '{package}'")
            return False

    # For updates, show diff and ask for confirmation
    if is_update and not noconfirm and old_version:
        new_version = aur_info(package).get("Version", "?") if aur_info(package) else "?"
        if not review_update(pkg_dir, package, old_version, new_version):
            warn(f"Update of {package} cancelled by user")
            return False

    # Automatic security checks
    valid, warnings = auto_verify(pkg_dir)
    if not valid:
        err(f"Security check failed for '{package}'")
        return False

    # Interactive review (unless noconfirm or quick mode with no warnings)
    if not noconfirm:
        if verify_level == "quick" and not warnings:
            # Quick mode with no warnings: proceed silently
            pass
        elif not interactive_review(pkg_dir, package, warnings, verify_level):
            warn(f"Installation of {package} cancelled by user")
            return False

    # Clean up backup file
    if backup_pkgbuild and backup_pkgbuild.exists():
        backup_pkgbuild.unlink()

    # Build the package - simple execution, all output goes directly to terminal
    info(f"Building {CYAN}{package}{R}...")
    cmd = ["makepkg", "-si"]
    if noconfirm:
        cmd.append("--noconfirm")

    result = subprocess.run(cmd, cwd=pkg_dir)

    if result.returncode != 0:
        err(f"makepkg failed for '{package}'")
        return False

    return True


def cmd_install(package: str, config: dict, noconfirm: bool = False, 
                no_verify: bool = False, cli_verify_level: str = None):
    aur_dir = get_aur_dir(config)
    verify_level = get_verify_level(config, no_verify, cli_verify_level)

    # Display verification level
    info(f"Verification level: {get_verify_level_display(verify_level)}")

    # Check if package is already installed
    if is_installed(package):
        ok(f"'{package}' is already installed. Use -Syu to update.")
        return

    # Check if package is in official repos
    if is_pacman_package(package):
        warn(f"'{package}' is available in the official repos.")
        warn(f"Use: sudo pacman -S {package}")
        sys.exit(0)

    # Check base-devel (only needed for actual installation)
    if not ensure_base_devel(noconfirm):
        err("base-devel is required for building AUR packages.")
        sys.exit(1)

    info(f"Resolving dependencies for '{CYAN}{package}{R}'...")
    if aur_info(package) is None:
        err(f"Package '{package}' not found on AUR.")
        sys.exit(1)

    deps = resolve_aur_deps(package)
    install_order = deps + [package]

    print(f"\n{BOLD}[auro]{R} Install order:")
    for i, pkg in enumerate(install_order, 1):
        marker = f"{GREEN}→{R}" if pkg == package else f"{GRAY}dep{R}"
        print(f"  {i}. [{marker}] {CYAN}{pkg}{R}  {GRAY}({AUR_GIT.format(pkg)}){R}")

    print()
    if not noconfirm:
        confirm = input(f"{BOLD}Proceed with installation?{R} [Y/n] ").strip().lower()
        if confirm not in ("", "y", "yes"):
            warn("Aborted.")
            return

    for pkg in install_order:
        if not clone_and_build(pkg, aur_dir, noconfirm, verify_level):
            err(f"Aborting due to error on '{pkg}'.")
            sys.exit(1)

    ok(f"'{package}' installed successfully.")


# ─── Update ───────────────────────────────────────────────────────────────────

def cmd_update(config: dict, package: str = None, noconfirm: bool = False, 
               no_verify: bool = False, cli_verify_level: str = None):
    aur_dir = get_aur_dir(config)
    verify_level = get_verify_level(config, no_verify, cli_verify_level)

    # Display verification level
    info(f"Verification level: {get_verify_level_display(verify_level)}")

    if package:
        if not is_installed(package):
            err(f"'{package}' is not installed.")
            sys.exit(1)
        pkg_dir = aur_dir / package
        if not pkg_dir.exists():
            err(f"'{package}' not found in {aur_dir}. Was it installed with auro?")
            sys.exit(1)
        local_ver = installed_version(package)
        pkg_info  = aur_info(package)
        if pkg_info is None:
            err(f"'{package}' not found on AUR.")
            sys.exit(1)
        aur_ver = pkg_info.get("Version", "?")
        if local_ver == aur_ver:
            ok(f"'{package}' is already up to date ({local_ver}).")
            return
        info(f"Updating {CYAN}{package}{R}: {YELLW}{local_ver}{R} → {GREEN}{aur_ver}{R}")
        if clone_and_build(package, aur_dir, noconfirm, verify_level, is_update=True, old_version=local_ver):
            ok(f"'{package}' updated successfully.")
        return

    # All packages
    if not aur_dir.exists():
        info("No AUR packages found.")
        return

    packages = [d.name for d in aur_dir.iterdir() if d.is_dir() and (d / ".git").exists()]
    if not packages:
        info("No AUR packages to update.")
        return

    info(f"Checking {len(packages)} AUR package(s) for updates...\n")

    to_update = []
    updates_info = []  # (pkg, local_ver, aur_ver)
    for pkg in packages:
        if not is_installed(pkg):
            print(f"  {GRAY}{pkg}: not installed (skipping — use -R or -C to clean){R}")
            continue
        local_ver = installed_version(pkg)
        pkg_info  = aur_info(pkg)
        if pkg_info is None:
            print(f"  {GRAY}{pkg}: not found on AUR (skipping){R}")
            continue
        aur_ver = pkg_info.get("Version", "?")
        if local_ver != aur_ver:
            print(f"  {CYAN}{pkg}{R}: {YELLW}{local_ver}{R} → {GREEN}{aur_ver}{R}  {BOLD}[UPDATE]{R}")
            to_update.append(pkg)
            updates_info.append((pkg, local_ver, aur_ver))
        else:
            print(f"  {GRAY}{pkg}: {local_ver} (up to date){R}")

    if not to_update:
        ok("All packages are up to date.")
        return

    print(f"\n{BOLD}[auro]{R} {len(to_update)} package(s) to update: {CYAN}{', '.join(to_update)}{R}")
    if not noconfirm:
        confirm = input(f"{BOLD}Proceed?{R} [Y/n] ").strip().lower()
        if confirm not in ("", "y", "yes"):
            warn("Aborted.")
            return

    for pkg, local_ver, aur_ver in updates_info:
        if not clone_and_build(pkg, aur_dir, noconfirm, verify_level, is_update=True, old_version=local_ver):
            err(f"Failed to update {pkg}")
            # Continue with other packages? Ask user
            if not noconfirm:
                cont = input(f"{BOLD}Continue with remaining updates?{R} [Y/n]: ").strip().lower()
                if cont not in ("", "y", "yes"):
                    break

    ok("Update complete.")


# ─── Search ───────────────────────────────────────────────────────────────────

def cmd_search(query: str):
    # Handle multi-word queries properly
    search_query = " ".join(query) if isinstance(query, list) else query

    try:
        r = requests.get(f"{AUR_RPC}/search/{search_query}", timeout=10)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        err(f"AUR API error: {e}")
        sys.exit(1)

    results = data.get("results", [])
    if not results:
        warn(f"No results for '{search_query}'.")
        return

    results.sort(key=lambda x: x.get("NumVotes", 0), reverse=True)

    info(f"{len(results)} result(s) for '{CYAN}{search_query}{R}':\n")
    for pkg in results:
        name        = pkg.get("Name", "?")
        version     = pkg.get("Version", "?")
        votes       = pkg.get("NumVotes", 0)
        description = pkg.get("Description") or ""
        inst        = f" {YELLW}[installed]{R}" if is_installed(name) else ""
        print(f"  {CYAN}{BOLD}{name}{R} {GREEN}{version}{R}  {GRAY}(votes: {votes}){R}{inst}")
        if description:
            print(f"    {description}")
        print()


# ─── Remove ───────────────────────────────────────────────────────────────────

def cmd_remove(package: str, config: dict, noconfirm: bool = False):
    if not is_installed(package):
        err(f"'{package}' is not installed.")
        sys.exit(1)

    info(f"Removing '{CYAN}{package}{R}'...")
    result = subprocess.run(["sudo", "pacman", "-R", package])

    if result.returncode != 0:
        err(f"Failed to remove {package}")
        sys.exit(1)

    aur_dir = get_aur_dir(config)
    pkg_dir = aur_dir / package
    if pkg_dir.exists():
        if noconfirm:
            shutil.rmtree(pkg_dir)
            ok(f"Source directory deleted.")
        else:
            confirm = input(f"{BOLD}Delete source directory{R} {CYAN}{pkg_dir}{R}? [Y/n] ").strip().lower()
            if confirm in ("", "y", "yes"):
                shutil.rmtree(pkg_dir)
                ok(f"Source directory deleted.")
            else:
                warn(f"Source kept. Run 'auro -C' to clean orphaned sources later.")


# ─── Clean ────────────────────────────────────────────────────────────────────

def cmd_clean(config: dict, noconfirm: bool = False):
    aur_dir = get_aur_dir(config)

    if not aur_dir.exists():
        info("AUR directory is empty, nothing to clean.")
        return

    packages = [d for d in aur_dir.iterdir() if d.is_dir() and (d / ".git").exists()]
    if not packages:
        info("No source directories found.")
        return

    orphans = [d for d in packages if not is_installed(d.name)]

    if not orphans:
        ok("No orphaned source directories found.")
        return

    print(f"\n{BOLD}[auro]{R} Orphaned source directories ({len(orphans)}):\n")
    for d in orphans:
        print(f"  {RED}✕{R}  {CYAN}{d.name}{R}  {GRAY}({d}){R}")

    print()
    if noconfirm:
        for d in orphans:
            shutil.rmtree(d)
            info(f"Deleted {CYAN}{d.name}{R}")
        ok(f"{len(orphans)} orphaned director(ies) cleaned.")
    else:
        confirm = input(f"{BOLD}Delete all?{R} [Y/n] ").strip().lower()
        if confirm in ("", "y", "yes"):
            for d in orphans:
                shutil.rmtree(d)
                info(f"Deleted {CYAN}{d.name}{R}")
            ok(f"{len(orphans)} orphaned director(ies) cleaned.")
        else:
            warn("Aborted.")


# ─── List ─────────────────────────────────────────────────────────────────────

def cmd_list(config: dict):
    aur_dir = get_aur_dir(config)

    if not aur_dir.exists():
        info("No AUR packages found.")
        return

    packages = [d for d in aur_dir.iterdir() if d.is_dir() and (d / ".git").exists()]
    if not packages:
        info("No AUR packages found.")
        return

    packages.sort(key=lambda d: d.name)
    info(f"{len(packages)} AUR package(s) in {CYAN}{aur_dir}{R}:\n")

    for d in packages:
        name    = d.name
        ver     = installed_version(name)
        if ver:
            tag = f"{GREEN}[installed]{R} {GRAY}{ver}{R}"
        else:
            tag = f"{YELLW}[orphan]{R}"
        print(f"  {CYAN}{BOLD}{name}{R}  {tag}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="auro",
        description="Simple AUR package manager"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-S",   metavar="PACKAGE",            help="Install a package")
    group.add_argument("-Ss",  metavar="QUERY",   nargs="+", help="Search AUR for packages")
    group.add_argument("-Syu", metavar="PACKAGE", nargs="?", const=True,
                               help="Update one package or all if none specified")
    group.add_argument("-R",   metavar="PACKAGE",            help="Remove a package")
    group.add_argument("-C",   action="store_true",          help="Clean orphaned source directories")
    group.add_argument("-Q",   action="store_true",          help="List all AUR packages managed by auro")

    # Security/automation flags
    parser.add_argument("--noconfirm", action="store_true",
                       help="Skip all confirmations (dangerous - for scripts)")
    parser.add_argument("--no-verify", action="store_true",
                       help="Skip PKGBUILD verification (not recommended)")
    parser.add_argument("--verify-level", choices=["none", "quick", "normal", "paranoid"],
                       help="Override config verification level")

    args = parser.parse_args()
    config = load_config()

    if args.S:
        cmd_install(args.S, config, args.noconfirm, args.no_verify, args.verify_level)
    elif args.Ss:
        cmd_search(args.Ss)
    elif args.Syu is not None:
        pkg = args.Syu if isinstance(args.Syu, str) else None
        cmd_update(config, pkg, args.noconfirm, args.no_verify, args.verify_level)
    elif args.R:
        cmd_remove(args.R, config, args.noconfirm)
    elif args.C:
        cmd_clean(config, args.noconfirm)
    elif args.Q:
        cmd_list(config)


if __name__ == "__main__":
    main()
