"""Enforce boundaries on extracted modules; retain explicit legacy entry points."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []
for path in ROOT.rglob("*"):
    rel = path.relative_to(ROOT)
    if any(part in ("node_modules", ".git", "dist", "__pycache__") for part in rel.parts):
        continue
    if path.suffix in (".tsx", ".css", ".ts") and rel.parts[0] != "frontend":
        errors.append(f"UI file outside frontend: {rel}")
    if path.suffix != ".py" or not path.is_file():
        continue
    scoped = str(rel).startswith(("backend/app/", "backend/models/"))
    if not scoped:
        continue
    for node in ast.walk(ast.parse(path.read_text())):
        imports = []
        if isinstance(node, ast.Import):
            imports = [x.name for x in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports = [node.module or ""]
        for name in imports:
            if name.startswith(("frontend", "backend.backend", "app.api")) or name == "backend":
                errors.append(f"Presentation dependency in {rel}: {name}")
            if str(rel).startswith("backend/app/contracts/") and name.startswith(("models", "evaluator", "app.db", "app.security")):
                errors.append(f"Implementation dependency in contract {rel}: {name}")
if errors:
    raise SystemExit("\n".join(errors))
print("Repository boundaries passed")
