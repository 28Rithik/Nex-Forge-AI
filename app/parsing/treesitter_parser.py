from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript


@dataclass
class ParsedFile:
    path: str
    language: str
    hash: str
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]


class TreeSitterParser:
    def __init__(self) -> None:
        self._parser_cache: dict[str, Parser] = {}

    def parse_file(self, file_path: Path, language: str) -> ParsedFile:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            return self._parse_python(file_path, content)
        if suffix in {".js", ".jsx", ".ts", ".tsx"}:
            return self._parse_tree_sitter(file_path, content, language)
        return ParsedFile(
            path=str(file_path),
            language=language,
            hash=self._content_hash(content),
            entities=[],
            relations=[],
        )

    def parse_workspace(self, root: Path) -> list[dict[str, Any]]:
        parsed_files: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".js", ".ts", ".tsx"}:
                language = self._language_name_for_suffix(path.suffix.lower())
                parsed_files.append(asdict(self.parse_file(path, language)))
        return parsed_files

    def _parse_python(self, file_path: Path, content: str) -> ParsedFile:
        tree = ast.parse(content)
        entities: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        class_stack: list[str] = []

        def add_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            entities.append(
                {
                    "kind": "Function",
                    "name": node.name,
                    "signature": self._python_signature(node),
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "docstring": ast.get_docstring(node) or "",
                    "class_name": class_stack[-1] if class_stack else "",
                }
            )
            if class_stack:
                relations.append({"kind": "HAS_METHOD", "source": class_stack[-1], "target": node.name})

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
                add_function(node)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
                add_function(node)
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> Any:
                bases = [self._name_of(base) for base in node.bases if self._name_of(base)]
                entities.append(
                    {
                        "kind": "Class",
                        "name": node.name,
                        "bases": bases,
                        "start_line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                    }
                )
                for base in bases:
                    relations.append({"kind": "EXTENDS", "source": node.name, "target": base})
                class_stack.append(node.name)
                self.generic_visit(node)
                class_stack.pop()

            def visit_Import(self, node: ast.Import) -> Any:
                for alias in node.names:
                    entities.append({"kind": "Import", "module": alias.name, "name": alias.asname or alias.name})

            def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
                module = node.module or ""
                for alias in node.names:
                    entities.append({"kind": "Import", "module": module, "name": alias.asname or alias.name})

            def visit_Call(self, node: ast.Call) -> Any:
                callee = self._name_of(node.func)
                if callee:
                    relations.append({"kind": "CALLS", "source": class_stack[-1] if class_stack else "", "target": callee})
                self.generic_visit(node)

            def _name_of(self, node: ast.AST) -> str:
                if isinstance(node, ast.Name):
                    return node.id
                if isinstance(node, ast.Attribute):
                    return node.attr
                return ""

        Visitor().visit(tree)
        return ParsedFile(
            path=str(file_path),
            language="python",
            hash=self._content_hash(content),
            entities=entities,
            relations=relations,
        )

    def _parse_tree_sitter(self, file_path: Path, content: str, language: str) -> ParsedFile:
        parser = self._get_parser(file_path.suffix.lower())
        tree = parser.parse(content.encode("utf-8", errors="ignore"))
        entities: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []

        def walk(node: Any, class_name: str = "") -> None:
            node_type = node.type
            if node_type in {"function_declaration", "function", "method_definition", "generator_function_declaration", "arrow_function"}:
                name = self._node_text(node.child_by_field_name("name") or node.child_by_field_name("property") or node)
                if name:
                    entities.append(
                        {
                            "kind": "Function",
                            "name": name,
                            "signature": self._signature_from_node(node),
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "docstring": "",
                            "class_name": class_name,
                        }
                    )
                    if class_name:
                        relations.append({"kind": "HAS_METHOD", "source": class_name, "target": name})
            elif node_type in {"class_declaration", "class_definition"}:
                name = self._node_text(node.child_by_field_name("name") or node)
                if name:
                    bases = self._collect_bases(node)
                    entities.append(
                        {
                            "kind": "Class",
                            "name": name,
                            "bases": bases,
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                        }
                    )
                    for base in bases:
                        relations.append({"kind": "EXTENDS", "source": name, "target": base})
                    class_name = name
            elif node_type in {"import_statement", "import_from_statement"}:
                module = self._collect_import_module(node)
                if module:
                    entities.append({"kind": "Import", "module": module, "name": module})
            elif node_type in {"call_expression", "new_expression"}:
                callee = self._node_text(node.child_by_field_name("function") or node.child_by_field_name("constructor") or node.child_by_field_name("name") or node)
                if callee:
                    relations.append({"kind": "CALLS", "source": class_name, "target": callee})

            for child in getattr(node, "named_children", []):
                walk(child, class_name)

        walk(tree.root_node)
        return ParsedFile(
            path=str(file_path),
            language=language,
            hash=self._content_hash(content),
            entities=entities,
            relations=relations,
        )

    def _collect_bases(self, node: Any) -> list[str]:
        bases: list[str] = []
        for child in getattr(node, "named_children", []):
            if child.type in {"class_heritage", "extends_clause", "superclass"}:
                text = self._node_text(child).replace("extends", "").strip()
                if text:
                    bases.append(text)
        return bases

    def _collect_import_module(self, node: Any) -> str:
        return self._node_text(node).strip()

    def _signature_from_node(self, node: Any) -> str:
        params = node.child_by_field_name("parameters")
        if params is None:
            return "()"
        text = self._node_text(params)
        return text if text.startswith("(") else f"({text})"

    def _python_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = [arg.arg for arg in node.args.args]
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        return f"({', '.join(args)})"

    def _node_text(self, node: Any) -> str:
        if node is None or not getattr(node, "text", None):
            return ""
        return node.text.decode("utf-8", errors="ignore")

    def _content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()

    def _get_parser(self, suffix: str) -> Parser:
        parser = self._parser_cache.get(suffix)
        if parser is not None:
            return parser

        parser = Parser()
        if suffix == ".py":
            parser.language = Language(tree_sitter_python.language())
        elif suffix in {".js", ".jsx"}:
            parser.language = Language(tree_sitter_javascript.language())
        elif suffix == ".ts":
            parser.language = Language(tree_sitter_typescript.language_typescript())
        else:
            parser.language = Language(tree_sitter_typescript.language_tsx())
        self._parser_cache[suffix] = parser
        return parser

    def _language_name_for_suffix(self, suffix: str) -> str:
        if suffix == ".py":
            return "python"
        if suffix in {".js", ".jsx"}:
            return "javascript"
        return "typescript"
