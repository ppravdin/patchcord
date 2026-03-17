#!/bin/bash
# Test folder detection heuristics for the installer.
# Creates temp dirs, runs detection logic, reports pass/fail.
set -euo pipefail

PASS=0
FAIL=0
TOTAL=0

check() {
  local label="$1" expected="$2" actual="$3"
  TOTAL=$((TOTAL + 1))
  if [ "$expected" = "$actual" ]; then
    echo "  ✓ $label"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $label (expected=$expected got=$actual)"
    FAIL=$((FAIL + 1))
  fi
}

# Detection logic (mirrors what installer should do)
detect() {
  local dir="$1"
  local home="$2"

  # Home / root
  if [ "$dir" = "$home" ] || [ "$dir" = "$home/" ] || [ "$dir" = "/" ]; then
    echo "HOME"
    return
  fi

  # Project markers — just check the dir itself, never scan subdirs
  local markers=".git package.json package-lock.json Cargo.toml go.mod go.sum pyproject.toml
    pom.xml build.gradle Makefile CMakeLists.txt Gemfile composer.json mix.exs
    requirements.txt setup.py .claude .codex .cursor .vscode"
  for m in $markers; do
    if [ -e "$dir/$m" ]; then
      echo "PROJECT"
      return
    fi
  done

  # Empty dir (no files, no subdirs) — valid new project
  local count
  count=$(ls -A "$dir" 2>/dev/null | wc -l)
  if [ "$count" -eq 0 ]; then
    echo "EMPTY"
    return
  fi

  # Has only dirs, no files at top level — looks like a projects container
  local file_count dir_count
  file_count=$(find "$dir" -maxdepth 1 -not -path "$dir" -type f | wc -l)
  dir_count=$(find "$dir" -maxdepth 1 -not -path "$dir" -type d | wc -l)
  if [ "$file_count" -eq 0 ] && [ "$dir_count" -ge 2 ]; then
    echo "CONTAINER"
    return
  fi

  # Ambiguous — has some files but no project markers
  echo "UNKNOWN"
}

# ─── Setup temp dirs ───────────────────────────────

BASE=$(mktemp -d)
FAKE_HOME="$BASE/fakehome"
mkdir -p "$FAKE_HOME"

echo "Testing folder detection..."
echo ""

# ─── Test 1: Home directory ────────────────────────
echo "Group 1: Home directory detection"
check "home dir" "HOME" "$(detect "$FAKE_HOME" "$FAKE_HOME")"
check "home dir with trailing slash" "HOME" "$(detect "$FAKE_HOME/" "$FAKE_HOME")"
check "root /" "HOME" "$(detect "/" "/")"

# ─── Test 2: Real project folders ──────────────────
echo ""
echo "Group 2: Real project folders"

P="$BASE/git-project"
mkdir -p "$P" && mkdir "$P/.git"
check "has .git" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/node-project"
mkdir -p "$P" && echo '{}' > "$P/package.json"
check "has package.json" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/rust-project"
mkdir -p "$P" && touch "$P/Cargo.toml"
check "has Cargo.toml" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/go-project"
mkdir -p "$P" && touch "$P/go.mod"
check "has go.mod" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/python-project"
mkdir -p "$P" && touch "$P/pyproject.toml"
check "has pyproject.toml" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/python-req-project"
mkdir -p "$P" && touch "$P/requirements.txt"
check "has requirements.txt" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/makefile-project"
mkdir -p "$P" && touch "$P/Makefile"
check "has Makefile" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/claude-project"
mkdir -p "$P/.claude"
check "has .claude dir" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/vscode-project"
mkdir -p "$P/.vscode"
check "has .vscode dir" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/java-project"
mkdir -p "$P" && touch "$P/pom.xml"
check "has pom.xml" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/ruby-project"
mkdir -p "$P" && touch "$P/Gemfile"
check "has Gemfile" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 3: Empty folder (new project) ───────────
echo ""
echo "Group 3: Empty folder (valid new project)"

P="$BASE/empty-project"
mkdir -p "$P"
check "empty dir" "EMPTY" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 4: Projects container ───────────────────
echo ""
echo "Group 4: Projects container (dirs only, no markers)"

P="$BASE/projects-container"
mkdir -p "$P/backend" "$P/frontend" "$P/mobile"
check "3 subdirs, no files" "CONTAINER" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/code-container"
mkdir -p "$P/project-a" "$P/project-b"
check "2 subdirs, no files" "CONTAINER" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 5: Not a container (has files) ──────────
echo ""
echo "Group 5: Ambiguous (files but no project markers)"

P="$BASE/random-dir"
mkdir -p "$P" && echo "hello" > "$P/notes.txt"
check "has files, no markers" "UNKNOWN" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/mixed-dir"
mkdir -p "$P/subdir" && echo "x" > "$P/readme.md"
check "has files + subdirs, no markers" "UNKNOWN" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 6: Edge cases ──────────────────────────
echo ""
echo "Group 6: Edge cases"

P="$BASE/one-subdir-only"
mkdir -p "$P/only-child"
check "1 subdir only (not container)" "UNKNOWN" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/dotfiles-only"
mkdir -p "$P" && touch "$P/.bashrc" "$P/.zshrc"
check "only dotfiles (not project)" "UNKNOWN" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/git-plus-subs"
mkdir -p "$P/.git" "$P/src" "$P/docs"
check ".git + subdirs = project, not container" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 7: Common wrong-install locations ───────
echo ""
echo "Group 7: Common wrong-install locations"

mkdir -p "$FAKE_HOME/Desktop"
check "~/Desktop (empty)" "EMPTY" "$(detect "$FAKE_HOME/Desktop" "$FAKE_HOME")"

mkdir -p "$FAKE_HOME/Downloads" && touch "$FAKE_HOME/Downloads/report.pdf" "$FAKE_HOME/Downloads/photo.jpg"
check "~/Downloads (has files)" "UNKNOWN" "$(detect "$FAKE_HOME/Downloads" "$FAKE_HOME")"

mkdir -p "$FAKE_HOME/Documents/taxes" "$FAKE_HOME/Documents/receipts"
check "~/Documents (has subdirs)" "CONTAINER" "$(detect "$FAKE_HOME/Documents" "$FAKE_HOME")"

P="$FAKE_HOME/my-stuff"
mkdir -p "$P" && echo "x" > "$P/notes.txt"
check "~/my-stuff (not home, not project)" "UNKNOWN" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 8: Monorepo ────────────────────────────
echo ""
echo "Group 8: Monorepo"

P="$BASE/monorepo"
mkdir -p "$P/.git" "$P/packages/app-a" "$P/packages/app-b" "$P/apps/web"
echo '{}' > "$P/package.json"
check "monorepo with .git + packages/" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/turbo-monorepo"
mkdir -p "$P/.git" "$P/apps/frontend" "$P/apps/backend"
touch "$P/turbo.json"
check "turborepo root" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 9: Temp dirs ───────────────────────────
echo ""
echo "Group 9: Temp directories"

P="$BASE/tmp-test"
mkdir -p "$P"
check "/tmp/something (empty)" "EMPTY" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/tmp-with-files"
mkdir -p "$P" && echo "x" > "$P/test.py"
check "/tmp/something with files" "UNKNOWN" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 10: Container with project children ────
echo ""
echo "Group 10: Container with project children"

P="$BASE/code-dir"
mkdir -p "$P/backend/.git" "$P/frontend/.git"
check "~/code/ with project subdirs" "CONTAINER" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/projects-with-readme"
mkdir -p "$P/proj-a" "$P/proj-b"
echo "my projects" > "$P/README.md"
check "projects dir with README" "UNKNOWN" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 11: Hidden-only project ────────────────
echo ""
echo "Group 11: Hidden-only project (fresh clone)"

P="$BASE/fresh-clone"
mkdir -p "$P/.git"
check "only .git, no files" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/codex-project"
mkdir -p "$P/.codex"
check "only .codex dir" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/cursor-project"
mkdir -p "$P/.cursor"
check "only .cursor dir" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 12: Obviously wrong locations ──────────
echo ""
echo "Group 12: Obviously wrong locations"

P="$BASE/fake-node-modules"
mkdir -p "$P" && touch "$P/package.json"
# node_modules dir itself won't have package.json at its root normally
# but a dir NAMED node_modules with contents — should we detect?
# Actually node_modules is inside a project, user won't cd there
check "dir with package.json (still project)" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 13: Codespace/devcontainer paths ───────
echo ""
echo "Group 13: Codespace/devcontainer"

P="$BASE/workspaces-project"
mkdir -p "$P/.git" && echo '{}' > "$P/package.json"
check "/workspaces/project with .git" "PROJECT" "$(detect "$P" "$FAKE_HOME")"

P="$BASE/workspace-empty"
mkdir -p "$P"
check "/workspaces/project empty" "EMPTY" "$(detect "$P" "$FAKE_HOME")"

# ─── Test 14: Symlink to home ────────────────────
echo ""
echo "Group 14: Symlink edge cases"

LINK="$BASE/home-link"
ln -s "$FAKE_HOME" "$LINK"
# Symlink resolves to home — detect() gets the symlink path, not resolved
# This is a known limitation: symlink !== homedir string
check "symlink to home (known limitation: not detected)" "UNKNOWN" "$(detect "$LINK" "$FAKE_HOME")"

# Symlink to a project
P="$BASE/real-project"
mkdir -p "$P/.git"
PLINK="$BASE/project-link"
ln -s "$P" "$PLINK"
check "symlink to project with .git" "PROJECT" "$(detect "$PLINK" "$FAKE_HOME")"

# ─── Cleanup ──────────────────────────────────────
rm -rf "$BASE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: $PASS/$TOTAL passed, $FAIL failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
