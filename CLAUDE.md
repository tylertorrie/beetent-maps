# Notes for Claude Code

## Roadmap

- **Google Drive integration.** A future task is to extend
  `maketentgrid.py` so it can read field CSVs directly from Google Drive
  instead of requiring a local file. No CSVs are committed to this
  repo — the real ones live on Drive — so pulling them in directly
  removes the manual export step before each run. When the user
  mentions "Google Drive", "Drive integration", or pulling the CSV from
  Drive, this is the feature they mean. It is on the roadmap, not
  in-flight; don't propose it unprompted, but do extend the existing
  CSV input path (the GUI file picker and the `csv_file` CLI argument)
  when the user is ready to work on it.

## Platform

This project is primarily used on **Windows**, with Linux also supported.
On Windows the user typically runs shell scripts from **Git Bash**.

Git Bash is not part of a default Windows install. If the user does not
already have it, help them install it before anything else — without it
they cannot run `nuitka_compile.sh` or any of the other shell tooling
in this project. Git Bash ships with [Git for Windows]; the usual path
is to download the installer from <https://git-scm.com/download/win> and
accept the defaults (which include Git Bash and add it to the Start
menu). Alternatively, on a recent Windows it can be installed with
`winget install --id Git.Git -e`. After installing, the user should
launch "Git Bash" from the Start menu and `cd` into the project
directory.

## Building the self-contained executable

The shell script `nuitka_compile.sh` builds a standalone binary using
[Nuitka](https://nuitka.net/) so the program can run without a Python
install on the target machine. The user-facing instructions live in
`README.md` under "Building a self-contained binary"; this section
covers the operational details.

### What the script does

```
python -m nuitka maketentgrid.py --enable-plugin=tk-inter \
    -o maketentgrid.exe --follow-imports --standalone
```

If the build succeeds, the script renames Nuitka's `maketentgrid.dist`
output folder to `beetent_trimble/`. The script refuses to start if a
`beetent_trimble/` folder already exists — remove it before rebuilding.

The output executable is named `maketentgrid.exe` on every platform
(this is just the `-o` argument; on Linux it's still an ELF binary
despite the `.exe` suffix).

### Running the build

From the project directory:

- **Windows (Git Bash):** `./nuitka_compile.sh`
- **Linux:** `./nuitka_compile.sh`

Use the same Python interpreter that has Nuitka installed (see below).
If `python` is not the right one, the user may need `python3` or a
venv-activated shell.

### Installing Nuitka

Nuitka is not pinned in this repo and is not installed by default.
Before the first build, check whether it is available:

```
python -m nuitka --version
```

If it is missing, help the user install it. Two reasonable options:

1. **Install into the user's Python** (simple, fine on Windows):

   ```
   python -m pip install --user nuitka
   ```

2. **Set up a dedicated venv** (recommended if the user does not want
   Nuitka in their global site-packages, or if `pip install` is blocked
   by a system-managed Python on Linux):

   ```
   python -m venv .venv
   # Git Bash on Windows:
   source .venv/Scripts/activate
   # Linux:
   source .venv/bin/activate
   pip install nuitka
   ```

   The venv must then be activated in the same shell before running
   `./nuitka_compile.sh`. The `.venv/` directory is local to the
   project and should not be committed.

Nuitka also needs a working C compiler. On first run it will offer to
download a MinGW toolchain on Windows, which is usually the right
answer. On Linux it picks up the system `gcc`/`clang`.

### Verifying the build

After the script finishes, the distributable folder is `beetent_trimble/`
in the project root. The entry point inside is `maketentgrid.exe`. Quick
smoke tests:

- `./beetent_trimble/maketentgrid.exe --help` — should print the argparse
  usage.
- Run it against a CSV the user supplies — should produce a `TNT/` folder
  of output next to that CSV (or wherever it lives). This repo does not
  ship a sample CSV, so ask the user for one if you need to smoke-test.

To distribute, zip the `beetent_trimble/` folder.
