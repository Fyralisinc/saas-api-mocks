"""A tiny, dependency-free GraphQL query parser + field projector.

Fireflies' real API is GraphQL: clients ``POST /graphql`` a ``{query, variables}``
body and get back ONLY the fields they selected. To serve that faithfully without
pulling in ``graphql-core``, this module:

  * tokenizes + parses a GraphQL *query* document (the read subset — named
    operations, ``$variables``, field arguments, nested selection sets, list/object
    literals; commas are insignificant per the spec). Mutations/subscriptions,
    fragments, directives and aliases-on-leaves beyond the basics are out of scope
    (the connector read surface uses none) and raise/skip cleanly.
  * resolves ``$var`` argument references against the request's ``variables`` dict.
  * ``project(value, selections)`` trims a fully-built resolver object down to the
    requested selection set (recursively, list-aware) — exactly GraphQL field
    selection semantics, so a client asking ``{ id title date }`` gets those three
    keys and nothing else.

Parse failures raise ``GraphQLSyntaxError`` (the app maps it to a 400 GraphQL
error envelope).
"""
from __future__ import annotations

import re
from typing import Any, Optional

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>[\s,﻿]+)
    | (?P<comment>\#[^\n\r]*)
    | (?P<blockstring>\"\"\"(?:\\.|\"(?!\"\")|[^\\])*?\"\"\")
    | (?P<string>\"(?:\\.|[^"\\\n])*\")
    | (?P<float>-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+[eE][+-]?\d+)
    | (?P<int>-?\d+)
    | (?P<name>[_A-Za-z][_0-9A-Za-z]*)
    | (?P<spread>\.\.\.)
    | (?P<punct>[(){}\[\]:!$=@|&])
    """,
    re.VERBOSE,
)


class GraphQLSyntaxError(Exception):
    """A malformed GraphQL query document."""


class Field:
    __slots__ = ("name", "alias", "args", "selections")

    def __init__(self, name: str, alias: Optional[str], args: dict,
                 selections: Optional[list["Field"]]):
        self.name = name
        self.alias = alias
        self.args = args
        self.selections = selections

    @property
    def key(self) -> str:
        return self.alias or self.name


def _tokenize(src: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos, n = 0, len(src)
    while pos < n:
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise GraphQLSyntaxError(f"unexpected character {src[pos]!r} at {pos}")
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        out.append((kind, m.group()))
    return out


def _unescape_string(tok: str) -> str:
    if tok.startswith('"""'):
        return tok[3:-3]
    body = tok[1:-1]
    return re.sub(r"\\(.)", lambda mm: {
        "n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/",
    }.get(mm.group(1), mm.group(1)), body)


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], variables: dict):
        self.toks = tokens
        self.i = 0
        self.vars = variables or {}

    def _peek(self) -> Optional[tuple[str, str]]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self) -> tuple[str, str]:
        if self.i >= len(self.toks):
            raise GraphQLSyntaxError("unexpected end of query")
        t = self.toks[self.i]
        self.i += 1
        return t

    def _expect_punct(self, ch: str) -> None:
        t = self._next()
        if t[0] != "punct" or t[1] != ch:
            raise GraphQLSyntaxError(f"expected {ch!r}, got {t[1]!r}")

    def parse_document(self) -> list[Field]:
        t = self._peek()
        if t is None:
            raise GraphQLSyntaxError("empty query")
        # Optional operation keyword + name + variable definitions.
        if t[0] == "name" and t[1] in ("query", "mutation", "subscription"):
            self._next()
            nxt = self._peek()
            if nxt and nxt[0] == "name":          # operation name
                self._next()
            nxt = self._peek()
            if nxt and nxt == ("punct", "("):      # variable definitions — skip
                self._skip_balanced("(", ")")
        return self._parse_selection_set()

    def _skip_balanced(self, open_ch: str, close_ch: str) -> None:
        self._expect_punct(open_ch)
        depth = 1
        while depth:
            t = self._next()
            if t == ("punct", open_ch):
                depth += 1
            elif t == ("punct", close_ch):
                depth -= 1

    def _parse_selection_set(self) -> list[Field]:
        self._expect_punct("{")
        fields: list[Field] = []
        while True:
            t = self._peek()
            if t is None:
                raise GraphQLSyntaxError("unterminated selection set")
            if t == ("punct", "}"):
                self._next()
                break
            if t[0] == "spread":                   # fragment spread — unsupported, skip
                self._next()
                nt = self._peek()
                if nt and nt == ("name", "on"):
                    self._next(); self._next()      # `on TypeName`
                elif nt and nt[0] == "name":
                    self._next()                    # `...FragName`
                if self._peek() == ("punct", "{"):
                    self._skip_balanced("{", "}")
                continue
            fields.append(self._parse_field())
        return fields

    def _parse_field(self) -> Field:
        t = self._next()
        if t[0] != "name":
            raise GraphQLSyntaxError(f"expected field name, got {t[1]!r}")
        name = t[1]
        alias = None
        if self._peek() == ("punct", ":"):         # alias: name
            self._next()
            real = self._next()
            if real[0] != "name":
                raise GraphQLSyntaxError("expected field name after alias")
            alias, name = name, real[1]
        args: dict = {}
        if self._peek() == ("punct", "("):
            args = self._parse_arguments()
        # skip directives (@name(...))
        while self._peek() == ("punct", "@"):
            self._next(); self._next()
            if self._peek() == ("punct", "("):
                self._skip_balanced("(", ")")
        selections: Optional[list[Field]] = None
        if self._peek() == ("punct", "{"):
            selections = self._parse_selection_set()
        return Field(name, alias, args, selections)

    def _parse_arguments(self) -> dict:
        self._expect_punct("(")
        args: dict = {}
        while True:
            t = self._peek()
            if t == ("punct", ")"):
                self._next()
                break
            name_t = self._next()
            if name_t[0] != "name":
                raise GraphQLSyntaxError(f"expected argument name, got {name_t[1]!r}")
            self._expect_punct(":")
            args[name_t[1]] = self._parse_value()
        return args

    def _parse_value(self) -> Any:
        t = self._next()
        kind, text = t
        if kind == "punct" and text == "$":
            var_t = self._next()
            if var_t[0] != "name":
                raise GraphQLSyntaxError("expected variable name after $")
            return self.vars.get(var_t[1])
        if kind == "int":
            return int(text)
        if kind == "float":
            return float(text)
        if kind in ("string", "blockstring"):
            return _unescape_string(text)
        if kind == "name":
            if text == "true":
                return True
            if text == "false":
                return False
            if text == "null":
                return None
            return text  # enum value -> its literal name
        if kind == "punct" and text == "[":
            items = []
            while self._peek() != ("punct", "]"):
                items.append(self._parse_value())
            self._next()
            return items
        if kind == "punct" and text == "{":
            obj = {}
            while self._peek() != ("punct", "}"):
                k = self._next()
                if k[0] != "name":
                    raise GraphQLSyntaxError("expected object field name")
                self._expect_punct(":")
                obj[k[1]] = self._parse_value()
            self._next()
            return obj
        raise GraphQLSyntaxError(f"unexpected value token {text!r}")


def parse(query: str, variables: Optional[dict] = None) -> list[Field]:
    """Parse a GraphQL query document → its top-level selected fields."""
    if not query or not query.strip():
        raise GraphQLSyntaxError("empty query")
    return _Parser(_tokenize(query), variables or {}).parse_document()


def project(value: Any, selections: Optional[list[Field]]) -> Any:
    """Trim a resolver value to the requested selection set (GraphQL semantics)."""
    if selections is None:
        return value
    if value is None:
        return None
    if isinstance(value, list):
        return [project(v, selections) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for f in selections:
            if f.name == "__typename":
                continue
            out[f.key] = project(value.get(f.name), f.selections)
        return out
    return value
