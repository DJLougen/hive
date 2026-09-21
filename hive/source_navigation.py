"""Static re-export navigation for package ``__init__.py`` facades.

When the agent reads a package ``__init__.py`` that is a *pure facade* —
a docstring, explicit ``from … import …`` statements, and a literal
``__all__`` — the file forwards names from exactly one implementation
module. Reading that module next is mechanical, not reasoning, so the
harness can suggest it as the next ``read_file`` and let the existing
CPU routing rules (``suggested_read``) take the turn without a model
call.

Everything here is deliberately conservative and AST-only:

* no code is executed and no module is imported — ``ast.parse`` plus
  ``Path.is_file`` existence checks only;
* the facade must contain *nothing but* a docstring, non-wildcard
  ``ImportFrom`` nodes, and a literal ``__all__`` assignment — any
  runtime logic, ordinary ``import`` statements, dynamic imports, or
  unparseable source refuses the suggestion;
* every re-export must resolve to the *same* file inside the workdir —
  unresolved/external imports, multiple distinct targets, path escapes
  (including through symlinks), and self-references all refuse;
* the target's contents are never read — only its existence is checked.

The payoff is one avoided paid LLM call per facade hop: the model would
otherwise be asked "which file do I read next?" when the ``__init__.py``
already answers it.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Facades are a handful of import lines; anything larger is not a facade
# worth parsing on the hot path and is refused outright.
_MAX_FACADE_BYTES = 64 * 1024


def _inside(path: Path, root: Path) -> bool:
    """True when ``path`` stays inside ``root`` after symlink resolution."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _module_file(base: Path, name: str, root: Path) -> Path | None:
    """Resolve one dotted-name component under ``base`` to a file.

    Returns the ``.py`` file or package ``__init__.py`` for ``name``,
    ``None`` when neither exists, and refuses (``None``) when both exist —
    the ``.py``-vs-package choice is then ambiguous and left to the model.
    """
    if not name.isidentifier():
        return None
    module = base / f"{name}.py"
    package = base / name / "__init__.py"
    if module.is_file() and package.is_file():
        return None  # ambiguous: both a module and a package claim the name
    for candidate in (module, package):
        if candidate.is_file():
            resolved = candidate.resolve()
            return resolved if _inside(resolved, root) else None
    return None


def _resolve_module(base: Path, parts: tuple[str, ...], root: Path) -> Path | None:
    """Resolve a dotted module path anchored at directory ``base``.

    Intermediate components must be real packages (``__init__.py`` present);
    the final component may be a module or a package. Anything unresolved,
    ambiguous, or escaping ``root`` returns ``None``.
    """
    if not parts:
        return None
    current = base
    for part in parts[:-1]:
        pkg_init = (current / part / "__init__.py").resolve()
        if not part.isidentifier() or not pkg_init.is_file() or not _inside(pkg_init, root):
            return None
        current = pkg_init.parent
    return _module_file(current, parts[-1], root)


def _resolve_import(node: ast.ImportFrom, pkg_dir: Path, root: Path) -> Path | None:
    """Resolve one ``from … import …`` to the file it re-exports from."""
    if node.level:
        # Relative import: level 1 anchors at the facade's own package
        # directory; each extra dot climbs one parent — and each parent
        # climbed into must itself be a package inside the checkout,
        # otherwise the import is beyond the top-level package at runtime.
        base = pkg_dir
        for _ in range(node.level - 1):
            base = base.parent
            if not _inside(base.resolve(), root) \
                    or not (base / "__init__.py").is_file():
                return None
        if node.module:
            return _resolve_module(base, tuple(node.module.split(".")), root)
        # ``from . import sub`` — the names are the modules.
        names = [a.name for a in node.names]
        if len(names) != 1:
            return None  # several submodules = several targets
        return _module_file(base, names[0], root)
    # Absolute import: only a module that lives inside the checkout counts.
    if not node.module:
        return None
    return _resolve_module(root, tuple(node.module.split(".")), root)


def _literal_all(node: ast.stmt) -> bool:
    """True for ``__all__ = ["a", "b"]`` / ``__all__: list[str] = (…)``."""
    value: ast.expr | None
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign):
        targets, value = [node.target], node.value
    else:
        return False
    if value is None:
        return False
    if not all(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
        return False
    return (
        isinstance(value, (ast.List, ast.Tuple))
        and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in value.elts)
    )


def suggest_reexport(
    content: str,
    *,
    source_path: str,
    workdir: Path,
    files_read: list[str],
) -> str | None:
    """Suggest the single module a pure ``__init__.py`` facade re-exports.

    Parameters
    ----------
    content:
        The ``__init__.py`` source the agent already read.
    source_path:
        Repo-relative path of that file (must name ``__init__.py`` inside
        ``workdir``).
    workdir:
        The episode's working copy root.
    files_read:
        Repo-relative paths the agent has already read — a target already
        read (or the facade itself) is never suggested.

    Returns the repo-relative target path, or ``None`` when the facade is
    not provably a single-target re-export — in which case the decision
    stays with the model exactly as before.
    """
    root = Path(workdir).resolve()
    src = (root / source_path).resolve()
    if src.name != "__init__.py" or not src.is_file() or not _inside(src, root):
        return None
    if len(content.encode("utf-8", errors="replace")) > _MAX_FACADE_BYTES:
        return None
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None

    imports: list[ast.ImportFrom] = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue  # docstring
        if isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names):
                return None  # wildcard re-export: targets unknowable
            imports.append(node)
            continue
        if _literal_all(node):
            continue
        return None  # runtime logic, ordinary/dynamic import, anything else
    if not imports:
        return None

    resolved = [_resolve_import(node, src.parent, root) for node in imports]
    # Every import must resolve — a missed one means external or
    # unresolvable re-exports are mixed in — and all must converge on a
    # single file.
    if any(r is None for r in resolved) or len(set(resolved)) != 1:
        return None
    target = resolved[0]
    assert target is not None
    rel = str(target.relative_to(root))
    if rel == str(src.relative_to(root)):
        return None  # self-import — no forward progress
    read = {
        str((root / p).resolve().relative_to(root))
        for p in files_read
        if _inside((root / p).resolve(), root)
    }
    if rel in read:
        return None  # already read — suggesting it would loop
    return rel


__all__ = ["suggest_reexport"]
