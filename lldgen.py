#!/usr/bin/env python3
"""Generate PlantUML LLD class diagrams from a Java Git repository."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import javalang


BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts"}
SOURCE_PARTS = (("src", "main", "java"), ("src",))
JAVA_LANG_TYPES = {"String", "Object", "Integer", "Long", "Double", "Float",
                   "Boolean", "Character", "Byte", "Short", "Void"}
COLLECTION_TYPES = {"List", "Set", "Collection", "Iterable", "Map", "Queue",
                    "Deque", "Optional", "ArrayList", "HashSet", "HashMap"}


@dataclass
class JavaType:
    name: str
    package: str
    kind: str
    modifiers: set[str]
    extends: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    fields: list[tuple[str, str, str]] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name


def type_text(node) -> str:
    if node is None:
        return "void"
    name = getattr(node, "name", str(node))
    args = getattr(node, "arguments", None) or []
    rendered = []
    for arg in args:
        pattern = getattr(arg, "pattern_type", None)
        child = getattr(arg, "type", None)
        text = type_text(child) if child else "?"
        rendered.append(f"? {pattern} {text}" if pattern else text)
    suffix = f"<{', '.join(rendered)}>" if rendered else ""
    dims = "[]" * len(getattr(node, "dimensions", None) or [])
    sub = getattr(node, "sub_type", None)
    return f"{name}{suffix}{dims}" + (f".{type_text(sub)}" if sub else "")


def visibility(modifiers: set[str]) -> str:
    if "public" in modifiers: return "+"
    if "protected" in modifiers: return "#"
    if "private" in modifiers: return "-"
    return "~"


def parse_java(path: Path) -> list[JavaType]:
    try:
        tree = javalang.parse.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (javalang.parser.JavaSyntaxError, javalang.tokenizer.LexerError) as exc:
        print(f"warning: skipped {path}: {exc}", file=sys.stderr)
        return []
    package = tree.package.name if tree.package else ""
    results = []
    accepted = (javalang.tree.ClassDeclaration, javalang.tree.InterfaceDeclaration,
                javalang.tree.EnumDeclaration, javalang.tree.AnnotationDeclaration)
    declarations = []
    for declaration_type in accepted:
        declarations.extend(node for _, node in tree.filter(declaration_type))
    # javalang's filter accepts one class, not a tuple of classes.
    for decl in declarations:
        kind = "interface" if isinstance(decl, javalang.tree.InterfaceDeclaration) else \
               "enum" if isinstance(decl, javalang.tree.EnumDeclaration) else \
               "annotation" if isinstance(decl, javalang.tree.AnnotationDeclaration) else "class"
        item = JavaType(decl.name, package, kind, set(decl.modifiers or []))
        ext = getattr(decl, "extends", None)
        if ext:
            item.extends = [type_text(x) for x in ext] if isinstance(ext, list) else [type_text(ext)]
        item.implements = [type_text(x) for x in (getattr(decl, "implements", None) or [])]
        for member in decl.body:
            if isinstance(member, javalang.tree.FieldDeclaration):
                mods = set(member.modifiers or [])
                for var in member.declarators:
                    extra_dims = "[]" * len(var.dimensions or [])
                    item.fields.append((var.name, type_text(member.type) + extra_dims, visibility(mods)))
            elif isinstance(member, (javalang.tree.MethodDeclaration, javalang.tree.ConstructorDeclaration)):
                mods = set(member.modifiers or [])
                name = member.name
                return_type = "" if isinstance(member, javalang.tree.ConstructorDeclaration) else f": {type_text(member.return_type)}"
                params = []
                for p in member.parameters:
                    ptype = type_text(p.type) + ("..." if p.varargs else "")
                    params.append(f"{p.name}: {ptype}")
                abstract = " {abstract}" if "abstract" in mods else ""
                static = " {static}" if "static" in mods else ""
                item.methods.append(f"{visibility(mods)}{name}({', '.join(params)}){return_type}{static}{abstract}")
        results.append(item)
    return results


def base_types(type_name: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", type_name)
    return [x.rsplit(".", 1)[-1] for x in tokens if x.rsplit(".", 1)[-1] not in JAVA_LANG_TYPES | COLLECTION_TYPES]


def safe_id(qname: str) -> str:
    return re.sub(r"\W", "_", qname)


def plantuml(types: list[JavaType], title: str) -> str:
    by_simple: dict[str, list[JavaType]] = {}
    for item in types:
        by_simple.setdefault(item.name, []).append(item)
    def resolve(owner: JavaType, raw: str):
        simple = raw.split("<", 1)[0].replace("[]", "").rsplit(".", 1)[-1]
        choices = by_simple.get(simple, [])
        same_package = [x for x in choices if x.package == owner.package]
        return (same_package or choices)[0] if len(same_package or choices) == 1 else None

    lines = ["@startuml", "hide empty members", "skinparam classAttributeIconSize 0",
             "skinparam linetype ortho", f"title {title}"]
    for item in sorted(types, key=lambda x: x.qualified_name):
        stereotype = " <<annotation>>" if item.kind == "annotation" else ""
        lines.append(f'{item.kind if item.kind != "annotation" else "interface"} "{item.qualified_name}" as {safe_id(item.qualified_name)}{stereotype} {{')
        lines.extend(f"  {vis}{name}: {typ}" for name, typ, vis in item.fields)
        if item.fields and item.methods: lines.append("  --")
        lines.extend(f"  {method}" for method in item.methods)
        lines.append("}")
    edges = set()
    for item in types:
        child = safe_id(item.qualified_name)
        for raw in item.extends:
            parent = resolve(item, raw)
            if parent: edges.add(f"{safe_id(parent.qualified_name)} <|-- {child} : is-a")
        for raw in item.implements:
            parent = resolve(item, raw)
            if parent: edges.add(f"{safe_id(parent.qualified_name)} <|.. {child} : implements")
        for field_name, field_type, _ in item.fields:
            for candidate in base_types(field_type):
                target = resolve(item, candidate)
                if target and target.qualified_name != item.qualified_name:
                    many = ' "*"' if any(x in field_type for x in COLLECTION_TYPES) or "[]" in field_type else ""
                    edges.add(f'{child} o--{many} {safe_id(target.qualified_name)} : has-a ({field_name})')
    lines.extend(sorted(edges))
    return "\n".join(lines + ["@enduml", ""])


def normalize_url(url: str) -> tuple[str, str | None, str | None]:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    marker = "/-/tree/"
    if marker in path:
        repo_path, rest = path.split(marker, 1)
        ref, _, subpath = rest.partition("/")
        return f"{parsed.scheme}://{parsed.netloc}{repo_path}.git", ref, subpath or None
    match = re.match(r"(.+?)/(?:tree|blob)/([^/]+)(?:/(.*))?$", path)
    if parsed.netloc.endswith("github.com") and match:
        repo_path, ref, subpath = match.groups()
        return f"{parsed.scheme}://{parsed.netloc}{repo_path}.git", ref, subpath
    return url if url.endswith(".git") else url + ".git", None, None


def project_roots(root: Path, package_depth: int, single_diagram: bool) -> list[Path]:
    if single_diagram:
        return [root]
    roots = {p.parent for p in root.rglob("*") if p.name in BUILD_FILES and ".git" not in p.parts}
    if roots:
        # Keep leaf modules so parent aggregator POMs do not duplicate diagrams.
        return sorted([p for p in roots if not any(q != p and p in q.parents for q in roots)])
    java_files = list(root.rglob("*.java"))
    if not java_files:
        return [root]
    # Source-tree URLs commonly contain many independent LLD examples under a
    # shared reverse-domain package (e.g. com/acme/interviewquestions/atm).
    common = Path(os.path.commonpath([str(p.parent) for p in java_files]))
    grouped = set()
    for source in java_files:
        relative = source.parent.relative_to(common)
        parts = relative.parts[:package_depth]
        grouped.add(common.joinpath(*parts) if parts else common)
    if len(grouped) > 1:
        return sorted(grouped)
    return [root]


@dataclass
class Diagram:
    filename: str
    source: str
    type_count: int
    relative: str


def clone(repo_url: str, destination: Path, timeout: int | None = None) -> Path:
    """Shallow-clone repo_url and return the directory the URL points at."""
    clone_url, ref, subpath = normalize_url(repo_url)
    command = ["git", "clone", "--depth", "1", "--single-branch"]
    if ref: command += ["--branch", ref]
    command += [clone_url, str(destination)]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(command, check=True, timeout=timeout, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    scan_root = destination / subpath if subpath else destination
    if not scan_root.exists(): raise ValueError(f"URL subpath does not exist: {subpath}")
    return scan_root


def generate(scan_root: Path, package_depth: int = 2, single_diagram: bool = False) -> list[Diagram]:
    diagrams = []
    for project in project_roots(scan_root, package_depth, single_diagram):
        java_files = list(project.rglob("*.java"))
        types = [item for path in java_files for item in parse_java(path)]
        if not types: continue
        relative = project.relative_to(scan_root)
        name = "root" if str(relative) == "." else str(relative).replace("/", "-")
        filename = re.sub(r"[^\w.-]", "_", name) + ".puml"
        diagrams.append(Diagram(filename, plantuml(types, name), len(types), str(relative)))
    return diagrams


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_url", help="GitHub/GitLab repository or tree URL")
    parser.add_argument("-o", "--output", default="lld-output", help="output directory")
    parser.add_argument("--keep-repo", action="store_true", help="retain cloned repository in output/repository")
    parser.add_argument("--package-depth", type=int, default=2,
                        help="package levels per project for source-tree URLs (default: 2)")
    parser.add_argument("--single-diagram", action="store_true",
                        help="combine every Java type into one diagram")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lldgen-") as temp:
        repo = Path(temp) / "repo"
        try:
            scan_root = clone(args.repo_url, repo)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"git clone failed: {exc.stderr.decode(errors='replace').strip()}")
        except ValueError as exc:
            raise SystemExit(str(exc))
        diagrams = generate(scan_root, args.package_depth, args.single_diagram)
        index = ["# Generated LLD diagrams", ""]
        for diagram in diagrams:
            (output / diagram.filename).write_text(diagram.source, encoding="utf-8")
            index.append(f"- `{diagram.filename}` — {diagram.type_count} types from `{diagram.relative}`")
        if args.keep_repo: shutil.copytree(repo, output / "repository", dirs_exist_ok=True)
        (output / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"Generated {len(diagrams)} diagram(s) in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
