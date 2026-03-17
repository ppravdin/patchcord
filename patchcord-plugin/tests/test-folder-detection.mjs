#!/usr/bin/env node
/**
 * Test folder detection heuristics using only Node.js fs APIs.
 * No shell commands, no find, no ls — just existsSync and readdirSync.
 */

import { existsSync, mkdirSync, writeFileSync, symlinkSync, readdirSync, rmSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";

const PROJECT_MARKERS = [
  ".git", "package.json", "package-lock.json", "Cargo.toml", "go.mod", "go.sum",
  "pyproject.toml", "pom.xml", "build.gradle", "Makefile", "CMakeLists.txt",
  "Gemfile", "composer.json", "mix.exs", "requirements.txt", "setup.py",
  ".claude", ".codex", ".cursor", ".vscode",
];

function detect(dir, home) {
  if (dir === home || dir === home + "/" || dir === "/") return "HOME";

  for (const m of PROJECT_MARKERS) {
    if (existsSync(join(dir, m))) return "PROJECT";
  }

  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return "UNKNOWN";
  }

  if (entries.length === 0) return "EMPTY";

  const files = entries.filter(e => e.isFile());
  const dirs = entries.filter(e => e.isDirectory());

  if (files.length === 0 && dirs.length >= 2) return "CONTAINER";

  return "UNKNOWN";
}

// ─── Test runner ─────────────────────────────────

let pass = 0, fail = 0, total = 0;

function check(label, expected, actual) {
  total++;
  if (expected === actual) {
    console.log(`  ✓ ${label}`);
    pass++;
  } else {
    console.log(`  ✗ ${label} (expected=${expected} got=${actual})`);
    fail++;
  }
}

// ─── Setup ───────────────────────────────────────

const BASE = join(tmpdir(), `patchcord-test-${Date.now()}`);
const FAKE_HOME = join(BASE, "fakehome");
mkdirSync(FAKE_HOME, { recursive: true });

function mkp(...parts) {
  const p = join(BASE, ...parts);
  mkdirSync(p, { recursive: true });
  return p.split("/").slice(0, -parts.length + 1 || undefined).length ? join(BASE, parts[0]) : p;
}

// ─── Group 1: Home directory ─────────────────────
console.log("Group 1: Home directory detection");
check("home dir", "HOME", detect(FAKE_HOME, FAKE_HOME));
check("home dir with trailing slash", "HOME", detect(FAKE_HOME + "/", FAKE_HOME));
check("root /", "HOME", detect("/", "/"));

// ─── Group 2: Real project folders ───────────────
console.log("\nGroup 2: Real project folders");

let p = join(BASE, "git-project");
mkdirSync(join(p, ".git"), { recursive: true });
check("has .git", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "node-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "package.json"), "{}");
check("has package.json", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "rust-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "Cargo.toml"), "");
check("has Cargo.toml", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "go-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "go.mod"), "");
check("has go.mod", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "python-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "pyproject.toml"), "");
check("has pyproject.toml", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "python-req");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "requirements.txt"), "");
check("has requirements.txt", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "makefile-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "Makefile"), "");
check("has Makefile", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "claude-project");
mkdirSync(join(p, ".claude"), { recursive: true });
check("has .claude dir", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "vscode-project");
mkdirSync(join(p, ".vscode"), { recursive: true });
check("has .vscode dir", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "java-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "pom.xml"), "");
check("has pom.xml", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "ruby-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "Gemfile"), "");
check("has Gemfile", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "composer-project");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "composer.json"), "");
check("has composer.json", "PROJECT", detect(p, FAKE_HOME));

// ─── Group 3: Empty folder ──────────────────────
console.log("\nGroup 3: Empty folder (valid new project)");

p = join(BASE, "empty-project");
mkdirSync(p, { recursive: true });
check("empty dir", "EMPTY", detect(p, FAKE_HOME));

// ─── Group 4: Projects container ─────────────────
console.log("\nGroup 4: Projects container (dirs only, no markers)");

p = join(BASE, "container-3");
mkdirSync(join(p, "backend"), { recursive: true });
mkdirSync(join(p, "frontend"));
mkdirSync(join(p, "mobile"));
check("3 subdirs, no files", "CONTAINER", detect(p, FAKE_HOME));

p = join(BASE, "container-2");
mkdirSync(join(p, "project-a"), { recursive: true });
mkdirSync(join(p, "project-b"));
check("2 subdirs, no files", "CONTAINER", detect(p, FAKE_HOME));

// ─── Group 5: Ambiguous ─────────────────────────
console.log("\nGroup 5: Ambiguous (files but no project markers)");

p = join(BASE, "random-dir");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "notes.txt"), "hello");
check("has files, no markers", "UNKNOWN", detect(p, FAKE_HOME));

p = join(BASE, "mixed-dir");
mkdirSync(join(p, "subdir"), { recursive: true });
writeFileSync(join(p, "readme.md"), "x");
check("has files + subdirs, no markers", "UNKNOWN", detect(p, FAKE_HOME));

// ─── Group 6: Edge cases ────────────────────────
console.log("\nGroup 6: Edge cases");

p = join(BASE, "one-subdir");
mkdirSync(join(p, "only-child"), { recursive: true });
check("1 subdir only (not container)", "UNKNOWN", detect(p, FAKE_HOME));

p = join(BASE, "dotfiles-only");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, ".bashrc"), "");
writeFileSync(join(p, ".zshrc"), "");
check("only dotfiles (not project)", "UNKNOWN", detect(p, FAKE_HOME));

p = join(BASE, "git-plus-subs");
mkdirSync(join(p, ".git"), { recursive: true });
mkdirSync(join(p, "src"));
mkdirSync(join(p, "docs"));
check(".git + subdirs = project, not container", "PROJECT", detect(p, FAKE_HOME));

// ─── Group 7: Common wrong-install locations ────
console.log("\nGroup 7: Common wrong-install locations");

p = join(FAKE_HOME, "Desktop");
mkdirSync(p, { recursive: true });
check("~/Desktop (empty)", "EMPTY", detect(p, FAKE_HOME));

p = join(FAKE_HOME, "Downloads");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "report.pdf"), "");
writeFileSync(join(p, "photo.jpg"), "");
check("~/Downloads (has files)", "UNKNOWN", detect(p, FAKE_HOME));

p = join(FAKE_HOME, "Documents");
mkdirSync(join(p, "taxes"), { recursive: true });
mkdirSync(join(p, "receipts"));
check("~/Documents (has subdirs)", "CONTAINER", detect(p, FAKE_HOME));

p = join(FAKE_HOME, "my-stuff");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "notes.txt"), "x");
check("~/my-stuff (not home, not project)", "UNKNOWN", detect(p, FAKE_HOME));

// ─── Group 8: Monorepo ──────────────────────────
console.log("\nGroup 8: Monorepo");

p = join(BASE, "monorepo");
mkdirSync(join(p, ".git"), { recursive: true });
mkdirSync(join(p, "packages", "app-a"), { recursive: true });
mkdirSync(join(p, "packages", "app-b"), { recursive: true });
writeFileSync(join(p, "package.json"), "{}");
check("monorepo with .git + packages/", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "turbo-monorepo");
mkdirSync(join(p, ".git"), { recursive: true });
mkdirSync(join(p, "apps", "frontend"), { recursive: true });
mkdirSync(join(p, "apps", "backend"), { recursive: true });
check("turborepo root (.git)", "PROJECT", detect(p, FAKE_HOME));

// ─── Group 9: Temp dirs ─────────────────────────
console.log("\nGroup 9: Temp directories");

p = join(BASE, "tmp-test");
mkdirSync(p, { recursive: true });
check("/tmp/something (empty)", "EMPTY", detect(p, FAKE_HOME));

p = join(BASE, "tmp-files");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "test.py"), "");
check("/tmp/something with files", "UNKNOWN", detect(p, FAKE_HOME));

// ─── Group 10: Container with project children ──
console.log("\nGroup 10: Container with project children");

p = join(BASE, "code-dir");
mkdirSync(join(p, "backend", ".git"), { recursive: true });
mkdirSync(join(p, "frontend", ".git"), { recursive: true });
check("~/code/ with project subdirs (no top-level markers)", "CONTAINER", detect(p, FAKE_HOME));

p = join(BASE, "projects-readme");
mkdirSync(join(p, "proj-a"), { recursive: true });
mkdirSync(join(p, "proj-b"));
writeFileSync(join(p, "README.md"), "my projects");
check("projects dir with README", "UNKNOWN", detect(p, FAKE_HOME));

// ─── Group 11: Hidden-only project ──────────────
console.log("\nGroup 11: Hidden-only project (fresh clone)");

p = join(BASE, "fresh-clone");
mkdirSync(join(p, ".git"), { recursive: true });
check("only .git, no files", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "codex-only");
mkdirSync(join(p, ".codex"), { recursive: true });
check("only .codex dir", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "cursor-only");
mkdirSync(join(p, ".cursor"), { recursive: true });
check("only .cursor dir", "PROJECT", detect(p, FAKE_HOME));

// ─── Group 12: Obviously wrong locations ────────
console.log("\nGroup 12: Obviously wrong locations");

p = join(BASE, "has-pkg-json");
mkdirSync(p, { recursive: true });
writeFileSync(join(p, "package.json"), "{}");
check("dir with package.json = project", "PROJECT", detect(p, FAKE_HOME));

// ─── Group 13: Codespace/devcontainer ───────────
console.log("\nGroup 13: Codespace/devcontainer");

p = join(BASE, "workspace-project");
mkdirSync(join(p, ".git"), { recursive: true });
writeFileSync(join(p, "package.json"), "{}");
check("/workspaces/project with .git", "PROJECT", detect(p, FAKE_HOME));

p = join(BASE, "workspace-empty");
mkdirSync(p, { recursive: true });
check("/workspaces/project empty", "EMPTY", detect(p, FAKE_HOME));

// ─── Group 14: Symlink edge cases ───────────────
console.log("\nGroup 14: Symlink edge cases");

const homeLink = join(BASE, "home-link");
symlinkSync(FAKE_HOME, homeLink);
check("symlink to home (sees subdirs = container)", "CONTAINER", detect(homeLink, FAKE_HOME));

p = join(BASE, "real-proj-for-link");
mkdirSync(join(p, ".git"), { recursive: true });
const projLink = join(BASE, "project-link");
symlinkSync(p, projLink);
check("symlink to project with .git", "PROJECT", detect(projLink, FAKE_HOME));

// ─── Cleanup ─────────────────────────────────────
rmSync(BASE, { recursive: true, force: true });

console.log("");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log(`Results: ${pass}/${total} passed, ${fail} failed`);
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
process.exit(fail > 0 ? 1 : 0);
