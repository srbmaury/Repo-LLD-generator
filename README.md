# Repository LLD Generator

Generates PlantUML class diagrams from Java repositories using a Java AST—not
regular expressions. It accepts normal GitHub/GitLab repository URLs and URLs
pointing to a branch subdirectory.

The generated diagrams contain:

- classes, interfaces, enums, and annotations;
- fields, constructors, and complete method signatures;
- `is-a` inheritance (`<|--`);
- interface implementation (`<|..`);
- resolved field-based `has-a` relationships (`o--`), with `*` multiplicity
  for collections and arrays.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python lldgen.py \
  "https://gitlab.com/shrayansh8/lld-lowleveldesign/-/tree/main/src?ref_type=heads" \
  --output diagrams
```

For a full repository:

```bash
python lldgen.py https://github.com/owner/repository --output diagrams
```

Each Maven/Gradle leaf module gets a separate `.puml` file. For a source-tree
URL, independent examples are inferred beneath the common Java package using
two package levels. Tune that boundary when a repository is organized
differently:

```bash
python lldgen.py REPOSITORY_OR_TREE_URL --package-depth 3
python lldgen.py REPOSITORY_OR_TREE_URL --single-diagram
```

## Render

Install PlantUML, then run:

```bash
plantuml diagrams/*.puml
```

Or open the `.puml` files with a PlantUML extension in IntelliJ or VS Code.

## Relationship semantics

Inheritance and implementation come directly from the AST. A field whose type
resolves to another repository class creates a `has-a` relationship. The tool
uses aggregation (`o--`) because source declarations alone cannot prove object
lifecycle ownership; claiming composition (`*--`) without constructor/data-flow
analysis would be inaccurate.

## Current scope

Java source is supported. Generated sources and syntactically invalid files are
skipped with a warning. Overloaded methods are retained individually. When two
classes have the same simple name, same-package resolution is preferred; an
ambiguous cross-package reference is omitted rather than guessed.
