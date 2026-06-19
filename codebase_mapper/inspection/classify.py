"""codebase_mapper.classify."""
from __future__ import annotations

import fnmatch
import re

from pathlib import Path, PurePosixPath

from ..shared_kernel.constants import ASSET_EXT, DATA_EXT, LANG_BY_EXT, MAN_PAGE_EXTS
from .models import FileRecord


def classify(path: str, content_head: bytes) -> str:
    p = PurePosixPath(path)
    name = p.name
    suffix = p.suffix.lower()
    parts = set(p.parts)

    if re.fullmatch(r"(LICENSE|LICENCE|COPYING|NOTICE|MIT-LICENSE|PATENTS)([._-].*)?", name, re.IGNORECASE):
        return "license"

    if name == "Dockerfile" or name.startswith("Dockerfile.") or name == "Containerfile":
        return "container"
    if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        return "container"

    if ".github" in parts and "workflows" in parts and suffix in {".yml", ".yaml"}:
        return "ci_cd"
    if name in {".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".travis.yml"}:
        return "ci_cd"
    if ".circleci" in parts:
        return "ci_cd"
    if "buildkite" in parts and suffix in {".yml", ".yaml"}:
        return "ci_cd"

    if name in {"poetry.lock", "Pipfile.lock", "package-lock.json", "yarn.lock",
                "Cargo.lock", "Gemfile.lock", "composer.lock", "pnpm-lock.yaml",
                "uv.lock",
                "go.sum",
                "pubspec.lock",
                "Package.resolved",
                "gradle.lockfile",
                "Podfile.lock",
                "Manifest.lock"}:
        return "lockfile"

    if name in {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
                "Pipfile", "package.json", "Cargo.toml", "go.mod", "Gemfile",
                "composer.json", "pom.xml", "Brewfile",
                "pubspec.yaml",
                "Package.swift",
                "build.gradle", "build.gradle.kts",
                "settings.gradle", "settings.gradle.kts",
                "Podfile"}:
        return "dependency_manifest"
    # CocoaPods .podspec files (Ruby DSL specs for iOS pods)
    if suffix == ".podspec":
        return "dependency_manifest"
    if re.fullmatch(r"requirements.*\.txt", name):
        return "dependency_manifest"
    if suffix == ".gemspec":
        return "dependency_manifest"

    if name in {"Makefile", "GNUmakefile", "CMakeLists.txt", "BUILD", "BUILD.bazel",
                "WORKSPACE", "Rakefile", "build.sh", "Justfile", "Taskfile.yml"}:
        return "build_script"
    if suffix in {".mk", ".cmake", ".bat", ".cmd"}:
        return "build_script"
    if suffix == ".rake":
        return "build_script"
    # CMake / autotools / pkg-config template files; MSBuild .targets/.props
    if name.endswith(".cmake.in") or name.endswith(".pc.in") or name.endswith(".in"):
        # The .in suffix is a build-system template marker. We over-match
        # generic .in here (could be anything), but in practice these are
        # almost always build templates.
        return "build_script"
    if suffix in {".targets", ".props"}:
        return "build_script"
    # bin/ entrypoints in Rails / Bundler / Rake / generic — usually shell or
    # Ruby bootstrap scripts that wrap a real command. Extensionless and in bin/.
    if ("bin" in parts or "exe" in parts or "tools" in parts) and not suffix and name in {
        "rails", "rake", "test", "console", "setup", "bundle", "bundler",
        "spring", "yarn", "webpack", "puma", "byebug", "ci",
        "devcontainer", "line_statistics", "railspect", "rdoc-to-md",
    }:
        return "build_script"
    # gradlew / gradlew.bat — Gradle wrapper. Lives at repo root or per-project.
    if name in {"gradlew", "gradlew.bat", "mvnw", "mvnw.cmd"}:
        return "build_script"

    if name in {".env", ".envrc", ".flaskenv"} or re.fullmatch(r"\.env\..+", name):
        return "environment"
    # Plain .env-suffixed files (e.g., test.env, production.env)
    if suffix == ".env":
        return "environment"

    # Dart codegen — build_runner / freezed / json_serializable / mockito /
    # auto_route / chopper / drift / retrofit / hive emit deterministic
    # suffix patterns. Classifying as 'generated' (instead of source_code
    # or test_code) makes them invisible to L2/L3/L4 by default — the
    # chunker, embedder, and LLM enricher all skip type_='generated'.
    # Must precede the test-code and source-code rules: a `foo.mocks.dart`
    # under `test/` is still generated noise, not a hand-written test.
    if suffix == ".dart":
        for marker in (".g.dart", ".freezed.dart", ".mocks.dart",
                       ".gr.dart", ".chopper.dart", ".config.dart",
                       ".part.dart", ".drift.dart", ".pb.dart",
                       ".pbenum.dart", ".pbjson.dart", ".pbserver.dart"):
            if name.endswith(marker):
                return "generated"

    # Minified web bundles — vendored libraries / build output committed to the
    # repo (e.g. public/pdf.worker.min.mjs, ~1.3 MB of pdf.js). A single such
    # blob otherwise classifies as source_code (its .js/.mjs suffix is a known
    # language) and yields thousands of chunks that dominate the concept graph
    # and bloat the semantic index. Classifying as 'generated' makes it invisible
    # to L2/L3/L4 (chunker, embedder, and LLM enricher all skip type_='generated').
    # Must precede the test-code and source-code rules: 'jquery.min.js' under a
    # tests/ directory is build output, not a hand-written test.
    if re.fullmatch(r".*\.min\.(js|cjs|mjs|css)", name, re.IGNORECASE):
        return "generated"

    # Test code — must precede source_code.
    if "tests" in parts or "test" in parts or "__tests__" in parts or "spec" in parts:
        if suffix in LANG_BY_EXT:
            return "test_code"
    if re.fullmatch(r"test_.*\.py|.*_test\.py", name):
        return "test_code"
    if re.fullmatch(r".*\.test\.(ts|tsx|js|jsx|cjs|mjs)", name):
        return "test_code"
    if re.fullmatch(r".*\.spec\.(ts|tsx|js|jsx|cjs|mjs|rb)", name):
        return "test_code"
    # Ruby: tests are often *_test.rb or *_spec.rb
    if re.fullmatch(r".*_(test|spec)\.rb", name):
        return "test_code"
    # Dart: *_test.dart is the canonical Flutter/Dart test naming.
    if re.fullmatch(r".*_test\.dart", name):
        return "test_code"
    # Java/Kotlin: FooTest.java / FooTests.java / FooIT.java (Integration
    # Test) — JUnit/TestNG/Spock conventions. Restrict to CamelCase
    # identifier endings so 'Latest.java' is NOT a test.
    if suffix in {".java", ".kt", ".kts"}:
        stem = p.stem
        if re.fullmatch(r"[A-Z][A-Za-z0-9]+?(?:Test|Tests|IT)", stem):
            return "test_code"
    # C++: foo_test.cc / foo_test.cpp / FooTest.cc / FooTest.cpp
    # (GoogleTest / Catch2 / Boost.Test). Restrict CamelCase form to
    # avoid claiming 'Latest.cpp' as a test.
    if suffix in {".cpp", ".cc", ".cxx", ".c++"}:
        stem = p.stem
        if re.fullmatch(r".*_test", stem) \
           or re.fullmatch(r"[A-Z][A-Za-z0-9]+?(?:Test|Tests)", stem):
            return "test_code"
    # Objective-C / Objective-C++: FooTests.m / FooTest.m / FooSpec.m
    # (XCTest / Specta / Kiwi conventions). Same Latest-safe CamelCase
    # guard. Apple's convention is plural ``*Tests.m`` more often than
    # singular; both are accepted.
    if suffix in {".m", ".mm"}:
        stem = p.stem
        if re.fullmatch(r"[A-Z][A-Za-z0-9]+?(?:Test|Tests|Spec|Specs)", stem):
            return "test_code"
    # Rust: tests/*.rs files are integration tests (cargo convention)
    if parts and "tests" in parts and suffix == ".rs":
        return "test_code"

    if suffix in {".md", ".rst", ".adoc", ".mdx", ".rdoc"}:
        return "documentation"
    if suffix in MAN_PAGE_EXTS:
        return "documentation"
    if suffix == ".cff":
        return "documentation"
    # Rails generators ship a USAGE file describing how to use the generator.
    if name == "USAGE":
        return "documentation"
    if "docs" in parts and suffix in {".txt", ".html"}:
        return "documentation"
    if re.fullmatch(
        r"(README|CHANGELOG|CHANGES|AUTHORS|CONTRIBUTORS|CONTRIBUTING|HISTORY|TODO|CODE_OF_CONDUCT|MAINTAINERS|GOVERNANCE)",
        name, re.IGNORECASE,
    ):
        return "documentation"

    if re.fullmatch(
        r"\.(gitignore|gitattributes|editorconfig|prettierrc|prettierignore|"
        r"eslintrc|eslintignore|flake8|pylintrc|isort\.cfg|coveragerc|"
        r"gitmodules|mailmap|dockerignore|nvmrc|node-version|python-version|"
        r"ruby-version|tool-versions|npmrc|yarnrc|pre-commit-config\.yaml|"
        r"readthedocs\.yaml|readthedocs\.yml|rubocop\.yml|rspec|simplecov|"
        r"yardopts|rdoc_options|standard\.yml|reek\.yml|"
        r"git-blame-ignore-revs|mdlrc|keep|gitkeep|document|empty_directory|"
        r"watchmanconfig|vscodeignore|flowconfig|metadata|clang-format|"
        r"swiftpm|swift-version|swiftlint\.yml|swiftformat|clangd)", name,
    ):
        return "configuration"
    if name in {"py.typed", "CODEOWNERS", "OWNERS", ".keep", ".gitkeep",
                ".empty_directory", ".document",
                "package-list",
                "ci-npmrc", ".npmignore",
                ".swiftpm"}:
        return "configuration"
    # Xcode / Apple platform configs
    if suffix in {".plist", ".xcconfig", ".xcworkspacedata", ".pbxproj",
                  ".xcscheme", ".xcsettings", ".entitlements", ".storyboard",
                  ".xib", ".modulemap", ".xcfilelist", ".xcprivacy"}:
        return "configuration"
    # Java / Kotlin / Android properties
    if suffix in {".properties", ".http", ".factories", ".imports",
                  ".entityidfactory", ".globalstatementinterceptor",
                  ".databaseconnectionautoregistration", ".typemapper",
                  ".api"}:
        return "configuration"
    # Windows resource / manifest / ProGuard / IDE
    if suffix in {".rc", ".manifest", ".pro", ".code-workspace"}:
        return "configuration"
    # JetBrains Writerside docs (variable .list files, .topic chapter files, .tree TOC)
    if suffix in {".topic", ".tree", ".list"}:
        return "documentation"
    # Snapshot test fixtures (Jest / Vitest)
    if suffix in {".snap", ".snapshot"}:
        return "data"
    # CocoaPods generated acknowledgements (committed but auto-generated)
    if name.endswith("-acknowledgements.markdown") or name.endswith("-acknowledgements.plist"):
        return "generated"
    # Precompiled header config
    if suffix == ".pch":
        return "configuration"
    # Git merge-conflict backups committed for testing purposes
    if suffix == ".orig":
        return "data"
    # Flow config / git hooks / similar — extensionless names with conventional meaning
    if name in {"flowconfig", "pre-commit", "pre-push", "post-commit", "commit-msg",
                "gitignore",  # template gitignore files (no leading dot)
                ".ignore",   # ripgrep / fd / generic
                ".swcrc",    # SWC compiler config
                ".alexignore", ".alexrc",
                ".cursorindexingignore",
                ".approvers",
                "install"}:
        return "configuration"
    # *.Dockerfile (suffix form, e.g. action.Dockerfile)
    if suffix == ".dockerfile":
        return "container"
    # *.approvers — CODEOWNERS-style approver lists for specific paths
    if suffix == ".approvers":
        return "configuration"
    # *.patch — git/quilt patch files (contain diffs)
    if suffix == ".patch":
        return "data"
    # *.xsd — W3C XML Schema Definition. Machine-readable shape/constraint
    # descriptions. Closer to structured fixture data than configuration.
    if suffix == ".xsd":
        return "data"
    if name in {"tsconfig.json", "tsconfig.base.json", "jsconfig.json",
                "rollup.config.js", "rollup.config.ts", "rollup.config.mjs",
                "vite.config.js", "vite.config.ts", "webpack.config.js",
                "babel.config.js", "babel.config.json", ".babelrc",
                "jest.config.js", "jest.config.ts", "jest.config.json",
                "vitest.config.js", "vitest.config.ts",
                "eslint.config.js", "eslint.config.mjs",
                "prettier.config.js", "prettier.config.cjs",
                "tailwind.config.js", "tailwind.config.ts",
                "postcss.config.js",
                "biome.json", "biome.jsonc",
                ".cursorrules", ".cursorignore",
                "_redirects", "_headers", "robots.txt",
                "site.webmanifest", "manifest.webmanifest",
                "vercel.json", "netlify.toml", "wrangler.toml",
                ".nojekyll", ".rspec", ".rubocop.yml", ".standard.yml"}:
        return "configuration"
    if ".husky" in parts or "husky" in parts:
        return "configuration"
    # Rails config conventions
    if "config" in parts and suffix in {".yml", ".yaml", ".rb"} and "rails" in path.lower():
        # Files in config/ in Rails apps are configuration. But this also catches
        # config.ru and the like - those go through other rules first.
        pass  # Don't classify aggressively; fall through

    if suffix in LANG_BY_EXT:
        return "source_code"

    if suffix in {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".json", ".json5", ".jsonc"}:
        return "configuration"
    if suffix in DATA_EXT:
        return "data"
    # .txt catch-all: typically fixtures, templates, samples
    if suffix == ".txt":
        return "data"
    if suffix in ASSET_EXT:
        return "asset"
    # ~-suffixed editor backup files: Rails has these committed deliberately
    # as test fixtures for malformed-template handling. Classify as data.
    if name.endswith("~"):
        return "data"
    # Source maps — almost always build outputs even when committed.
    if suffix == ".map" or name.endswith(".js.map") or name.endswith(".css.map"):
        return "generated"
    if b"\x00" in content_head:
        return "binary"
    # Late-stage path rule: anything in a fixture-bearing directory that
    # nothing else has claimed is by location a fixture — classify as data.
    parts_list = p.parts
    for i in range(len(parts_list) - 1):
        if (parts_list[i] in ("tests", "test", "__tests__")
            and parts_list[i+1] in ("snapshots", "fixtures")):
            return "data"
    # gin / Go convention: `testdata/` is a recognized test-fixture directory.
    if "testdata" in parts_list:
        return "data"
    # `Tests/<...>/Utilities/`-style fixture path (Swift Vapor convention).
    for i in range(len(parts_list) - 1):
        if parts_list[i] == "Tests" and "Utilities" in parts_list[i:]:
            return "data"
    # Plain-text version markers: RAILS_VERSION, VERSION, etc.
    if re.fullmatch(r"[A-Z][A-Z0-9_]*_?VERSION", name):
        return "data"
    # Extensionless shebang scripts (e.g. scripts/uninstall with #!/bin/sh)
    if not suffix and content_head[:2] == b"#!":
        return "source_code"
    return "unknown"

def language_of(path: str) -> str | None:
    p = PurePosixPath(path)
    lang = LANG_BY_EXT.get(p.suffix.lower())
    # config.ru and Rakefile etc. — extensionless Ruby files
    if lang is None and p.name in {"Rakefile", "Gemfile", "config.ru"}:
        return "ruby"
    return lang

def refine_phases(record: FileRecord) -> list[str]:
    p = PurePosixPath(record.path)
    parts = list(p.parts)
    name = p.name

    if name == "conf.py" and "docs" in parts:
        return ["build"]
    if record.type_ == "source_code" and "docs" in parts:
        return ["build", "dev"]
    if name in {"noxfile.py", "tasks.py", "setup.py", "fabfile.py"}:
        return ["build", "dev"]
    if name == "conftest.py":
        return ["test"]
    if record.type_ == "source_code" and parts and parts[0] in {"tools", "scripts"}:
        return ["build", "dev"]
    # Rust: build.rs is a build-time script
    if name == "build.rs":
        return ["build"]
    # Rust: examples/ files are dev/runtime example apps
    if record.type_ == "source_code" and parts and parts[0] == "examples":
        return ["dev", "runtime"]
    # Rust: benches/ is bench harness
    if record.type_ == "source_code" and parts and parts[0] == "benches":
        return ["test", "dev"]
    return record.phases

def path_excluded(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    for raw in patterns:
        # Trailing slash is a gitignore-style directory marker. Strip it —
        # paths from `git ls-tree` never end in "/", so the trailing slash
        # would otherwise prevent any match. ".repo/" => ".repo".
        pat = raw.rstrip("/")
        if not pat:
            continue
        if fnmatch.fnmatchcase(path, pat):
            return True
        # Explicit "dir/**" form: match the directory itself + every descendant.
        if pat.endswith("/**") and (path == pat[:-3] or path.startswith(pat[:-2])):
            return True
        # Convenience: a bare path with no wildcards (e.g. ".repo" or
        # "vendor/cache") also excludes everything under it. Lets users write
        # ".repo" instead of ".repo" + ".repo/**".
        if not any(ch in pat for ch in ("*", "?", "[")):
            if path == pat or path.startswith(pat + "/"):
                return True
    return False


def read_repo_ignore(repo: Path | str) -> list[str]:
    """Read per-repo ignore patterns from ``<repo>/.cbmignore``.

    One pattern per line; ``#`` comments and blank lines are skipped.
    Patterns use the same syntax as ``--exclude`` (fnmatch globs;
    bare paths exclude descendants too; gitignore-style trailing
    slashes are accepted). Returns ``[]`` if the file is absent
    or unreadable.
    """
    ignore_path = Path(repo) / ".cbmignore"
    if not ignore_path.is_file():
        return []
    try:
        text = ignore_path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out
