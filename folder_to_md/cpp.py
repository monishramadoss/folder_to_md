"""C/C++ parser built on tree-sitter."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import tree_sitter
import tree_sitter_cpp

from .model import Declaration, Definition, FileInfo
from .parser import LanguageParser

INCLUDE_QUERY = """
    (preproc_include path: (string_literal) @path)
    (preproc_include path: (system_lib_string) @path)
"""
DEF_QUERY = """
    (class_specifier name: (type_identifier) @class.name body: (field_declaration_list)) @class.def
    (struct_specifier name: (type_identifier) @class.name body: (field_declaration_list)) @class.def
    (function_definition) @function.def
"""
DECL_QUERY = """
    (declaration) @fn.decl
    (field_declaration) @fn.decl
    (class_specifier name: (type_identifier) @class.decl !body) @class.decl.node
    (struct_specifier name: (type_identifier) @class.decl !body) @class.decl.node
"""
CALL_QUERY = "(call_expression function: (_) @call.fn)"
TYPE_QUERY = "(type_identifier) @type"


def _node_text(node: tree_sitter.Node) -> str:
    return node.text.decode("utf-8", errors="replace")


class CppParser(LanguageParser):
    extensions = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})

    def __init__(self):
        self._language = tree_sitter.Language(tree_sitter_cpp.language())
        self._parser = tree_sitter.Parser(self._language)
        self._include_query = tree_sitter.Query(self._language, INCLUDE_QUERY)
        self._def_query = tree_sitter.Query(self._language, DEF_QUERY)
        self._decl_query = tree_sitter.Query(self._language, DECL_QUERY)
        self._call_query = tree_sitter.Query(self._language, CALL_QUERY)
        self._type_query = tree_sitter.Query(self._language, TYPE_QUERY)

    def parse(self, rel_path: Path, source: bytes) -> FileInfo:
        tree = self._parser.parse(source)
        root = tree.root_node
        info = FileInfo(path=rel_path, language="cpp")
        info.includes = self._includes(root)
        self._definitions(root, info)
        self._declarations(root, info)
        info.calls = self._calls(root)
        return info

    def _includes(self, root: tree_sitter.Node) -> list[str]:
        captures = tree_sitter.QueryCursor(self._include_query).captures(root)
        return [_node_text(n).strip('"<>') for n in captures.get("path", [])]

    def _definitions(self, root: tree_sitter.Node, info: FileInfo):
        for _, captures in tree_sitter.QueryCursor(self._def_query).matches(root):
            if "class.def" in captures:
                name = _node_text(captures["class.name"][0])
                node = captures["class.def"][0]
                info.class_defs.append(
                    Definition(
                        name=name,
                        snippet=_node_text(node),
                        calls=self._calls(node),
                        type_uses=self._type_uses(node, exclude=name),
                        bases=self._bases(node),
                    )
                )
            elif "function.def" in captures:
                node = captures["function.def"][0]
                name = self._function_name(node)
                if name:
                    info.fn_defs.append(
                        Definition(
                            name=name,
                            snippet=_node_text(node),
                            calls=self._calls(node),
                            type_uses=self._type_uses(node),
                        )
                    )

    def _declarations(self, root: tree_sitter.Node, info: FileInfo):
        seen_classes = set()
        for _, captures in tree_sitter.QueryCursor(self._decl_query).matches(root):
            if "class.decl" in captures:
                name = _node_text(captures["class.decl"][0])
                if name not in seen_classes:
                    seen_classes.add(name)
                    info.class_decls.append(
                        Declaration(
                            name=name,
                            snippet=_node_text(captures["class.decl.node"][0]),
                        )
                    )
            elif "fn.decl" in captures:
                node = captures["fn.decl"][0]
                name = self._function_name(node)
                if name:
                    info.fn_decls.append(
                        Declaration(name=name, snippet=_node_text(node))
                    )

    def _calls(self, node: tree_sitter.Node) -> Counter:
        calls = Counter()
        for _, captures in tree_sitter.QueryCursor(self._call_query).matches(node):
            name = self._call_name(captures["call.fn"][0])
            if name:
                calls[name] += 1
        return calls

    @staticmethod
    def _bases(class_node: tree_sitter.Node) -> list[str]:
        # names listed after the colon in `class Derived : public Base, Other`
        for child in class_node.named_children:
            if child.type == "base_class_clause":
                return [
                    _node_text(c)
                    for c in child.named_children
                    if c.type in ("type_identifier", "qualified_identifier",
                                  "template_type")
                ]
        return []

    def _type_uses(self, node: tree_sitter.Node, exclude: str = None) -> list[str]:
        # class/struct names referenced within the node, in order of appearance
        names = {}
        for _, captures in tree_sitter.QueryCursor(self._type_query).matches(node):
            text = _node_text(captures["type"][0])
            if text != exclude:
                names[text] = None
        return list(names)

    @staticmethod
    def _function_name(node: tree_sitter.Node) -> str | None:
        # unwrap pointer/reference declarators down to the function_declarator
        d = node.child_by_field_name("declarator")
        while d is not None and d.type != "function_declarator":
            d = d.child_by_field_name("declarator")
        if d is None:
            return None
        name = d.child_by_field_name("declarator")
        if name is None:
            return None
        text = _node_text(name)
        # function pointers like `int (*fp)(int);` are variables, not functions
        return None if "(" in text else text

    @staticmethod
    def _call_name(fn_node: tree_sitter.Node) -> str | None:
        if fn_node.type in ("identifier", "qualified_identifier"):
            return _node_text(fn_node)
        if fn_node.type == "field_expression":
            field = fn_node.child_by_field_name("field")
            return _node_text(field) if field else None
        if fn_node.type == "template_function":
            name = fn_node.child_by_field_name("name")
            return _node_text(name) if name else None
        return None
