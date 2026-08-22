"""Go origin classification helpers (ADR 0042 § Go).

Pure helpers used by the universal ``tree_sitter`` strategy and the
call-graph dispatcher in :mod:`weld.strategies._language_origin` to
stamp ``props.origin`` on every Go-language node.

Per ADR 0042's Go rule:

* **stdlib** — the import path is a member of the static Go
  standard-library set (``GO_STDLIB_PACKAGES``). The set is hard-coded
  rather than probed at runtime; the Go stdlib is stable across point
  releases (a minor-version bump may add new packages but never
  removes them) and the bundled discovery layer must run without a
  Go toolchain on PATH. The accepted list mirrors the canonical
  output of ``go list std`` at Go 1.22 trimmed to the ~50 packages
  that account for the overwhelming majority of real-world imports;
  follow-up additions are a one-line edit.
* **project** — the import path is exactly the module path declared
  in the project's ``go.mod`` *or* lives under that module path as a
  proper sub-package (``<module>/<sub>``). A bare prefix match is not
  enough: ``github.com/example/myapi-extras`` is NOT a sub-package of
  ``github.com/example/myapi``.
* **external** — the import path is neither stdlib nor project: a
  third-party module (``github.com/gin-gonic/gin``, ``go.uber.org/zap``).
  When the module path is unknown (no ``go.mod`` parsed), an import
  that does not match the static stdlib list still classifies as
  ``external`` because the import path itself is a definite signal
  even without project context.
* **unresolved** — only when the import path is empty (the caller
  could not capture a token at all).

The functions in this module are deterministic and pure: they read
only their arguments, do not touch the filesystem, do not import any
non-stdlib Python module, and never log. The fixture-based
acceptance test for the four-way classification lives in
:mod:`weld.tests.weld_go_origin_test` (see ``GoOriginFixtureTest``).
"""

from __future__ import annotations

#: The Go standard-library package set used for the ``stdlib``
#: classification. The list is the canonical output of ``go list std``
#: at Go 1.22 reduced to the ~50 packages that cover the imports
#: actually observed in real-world Go code (every package the
#: tree-sitter Go fixtures use, plus the runtime/concurrency/IO
#: cluster every Go service touches). The Go standard library is
#: append-only across minor versions, so this set is forward-compatible:
#: a future Go release can only *add* packages we have not classified,
#: in which case they fall through to ``external`` — a conservative
#: failure mode that never miscalls a project import as stdlib.
#:
#: When extending this set, prefer the dotted-slash form Go uses in
#: import declarations (``encoding/json``, not ``encoding.json``).
GO_STDLIB_PACKAGES: frozenset[str] = frozenset(
    {
        # Top-level: core
        "bufio",
        "bytes",
        "context",
        "errors",
        "expvar",
        "flag",
        "fmt",
        "io",
        "log",
        "math",
        "os",
        "path",
        "reflect",
        "regexp",
        "runtime",
        "sort",
        "strconv",
        "strings",
        "sync",
        "time",
        "unicode",
        "unsafe",
        "embed",
        "plugin",
        # crypto/*
        "crypto",
        "crypto/aes",
        "crypto/cipher",
        "crypto/hmac",
        "crypto/md5",
        "crypto/rand",
        "crypto/rsa",
        "crypto/sha1",
        "crypto/sha256",
        "crypto/sha512",
        "crypto/subtle",
        "crypto/tls",
        "crypto/x509",
        # encoding/*
        "encoding",
        "encoding/base64",
        "encoding/binary",
        "encoding/csv",
        "encoding/hex",
        "encoding/json",
        "encoding/pem",
        "encoding/xml",
        # io/*
        "io/fs",
        "io/ioutil",
        # net/*
        "net",
        "net/http",
        "net/http/httptest",
        "net/http/httputil",
        "net/mail",
        "net/rpc",
        "net/smtp",
        "net/textproto",
        "net/url",
        # os/*
        "os/exec",
        "os/signal",
        "os/user",
        # path/*
        "path/filepath",
        # log/*
        "log/slog",
        "log/syslog",
        # math/*
        "math/big",
        "math/bits",
        "math/cmplx",
        "math/rand",
        # sync/*
        "sync/atomic",
        # time/*
        # (none beyond time)
        # text/* and html/*
        "html",
        "html/template",
        "text/scanner",
        "text/tabwriter",
        "text/template",
        # database/*
        "database/sql",
        "database/sql/driver",
        # go/* (the Go AST family)
        "go/ast",
        "go/build",
        "go/constant",
        "go/doc",
        "go/format",
        "go/parser",
        "go/printer",
        "go/scanner",
        "go/token",
        "go/types",
        # archive/* and compress/*
        "archive/tar",
        "archive/zip",
        "compress/bzip2",
        "compress/flate",
        "compress/gzip",
        "compress/zlib",
        # debug/runtime helpers commonly imported
        "runtime/debug",
        "runtime/pprof",
        "runtime/trace",
        # testing
        "testing",
        "testing/fstest",
        "testing/iotest",
        "testing/quick",
        # mime
        "mime",
        "mime/multipart",
        "mime/quotedprintable",
        # container
        "container/heap",
        "container/list",
        "container/ring",
        # hash family
        "hash",
        "hash/adler32",
        "hash/crc32",
        "hash/crc64",
        "hash/fnv",
        "hash/maphash",
        # image (commonly imported, optional)
        "image",
        "image/color",
        "image/draw",
        "image/gif",
        "image/jpeg",
        "image/png",
    }
)


def strip_quotes(import_path: str) -> str:
    """Return *import_path* with surrounding double quotes removed.

    Tree-sitter's Go ``imports`` query captures the literal source
    token ``"fmt"``, including quotes. Callers may also pass the
    pre-stripped form. This helper accepts both shapes so the public
    API is forgiving -- and is idempotent, so re-applying it to an
    already-stripped path is a no-op.

    Public (not ``_strip_quotes``) because :func:`weld.strategies.
    _go_tree_sitter.strip_import_quotes` also calls it directly (bd
    bt5m) to clean ``props.imports_from`` itself, not just the
    classification this module already tolerated both forms for.
    """
    if (
        len(import_path) >= 2
        and import_path.startswith('"')
        and import_path.endswith('"')
    ):
        return import_path[1:-1]
    return import_path


def is_go_stdlib(import_path: str) -> bool:
    """Return True if *import_path* names a Go standard-library package.

    The match is exact against :data:`GO_STDLIB_PACKAGES`. The helper
    deliberately operates on the *unquoted* path because the stdlib
    set is stored unquoted; callers that pass tree-sitter capture
    text directly should use :func:`classify_go_import` (which
    strips quotes for them) rather than this predicate.
    """
    if not import_path:
        return False
    return import_path in GO_STDLIB_PACKAGES


def parse_go_mod_module_path(text: str) -> str:
    """Return the ``module`` directive value from *text*, or ``""``.

    The Go ``go.mod`` grammar permits the module directive as

        module <path>

    or

        module "<path>"

    optionally with leading whitespace. Comments use ``//`` line
    syntax. We do not need a full parser here: the directive must be
    on a line of its own, the keyword ``module`` is reserved at
    file scope, and only the first occurrence wins.

    The implementation is intentionally minimal so the helper stays
    pure (no third-party deps, no file I/O). It tolerates
    Windows-style line endings via ``str.splitlines``.
    """
    if not text:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        # Must start with the literal keyword ``module`` followed by
        # whitespace; ``modulefoo`` is not a directive.
        if not line.startswith("module"):
            continue
        rest = line[len("module") :]
        if not rest or not rest[0].isspace():
            continue
        path = rest.strip()
        # Strip an inline ``//`` trailing comment if present.
        comment = path.find("//")
        if comment != -1:
            path = path[:comment].rstrip()
        # Collapse the remaining surrounding quotes if any.
        if (
            len(path) >= 2
            and path.startswith('"')
            and path.endswith('"')
        ):
            path = path[1:-1]
        if path:
            return path
    return ""


def classify_go_import(import_path: str, module_path: str) -> str:
    """Return the ADR 0042 origin for a Go import.

    Args:
        import_path: The import path as captured by tree-sitter; the
            literal token (``"fmt"``) or the pre-stripped form
            (``fmt``) are both accepted.
        module_path: The module path declared in ``go.mod`` (the
            output of :func:`parse_go_mod_module_path`). Empty when
            no ``go.mod`` is available.

    Returns:
        One of ``"stdlib"`` / ``"project"`` / ``"external"`` /
        ``"unresolved"``. The function is total: malformed inputs
        always yield ``"unresolved"``.
    """
    path = strip_quotes(import_path).strip()
    if not path:
        return "unresolved"

    if path in GO_STDLIB_PACKAGES:
        return "stdlib"

    if module_path:
        # Project: equal to the module path or under it as a proper
        # sub-package. The slash boundary check rejects sibling
        # repositories with a shared literal prefix
        # (``github.com/example/myapi-extras`` vs
        # ``github.com/example/myapi``).
        if path == module_path:
            return "project"
        prefix = module_path.rstrip("/") + "/"
        if path.startswith(prefix):
            return "project"

    return "external"


__all__ = [
    "GO_STDLIB_PACKAGES",
    "classify_go_import",
    "is_go_stdlib",
    "parse_go_mod_module_path",
    "strip_quotes",
]
