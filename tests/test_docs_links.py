"""Link integrity for the documentation: every relative markdown link in README.md and docs/**/*.md
resolves to an existing file or directory, in-page and cross-file anchors exist, and every markdown
file under docs/ is reachable from docs/README.md (no orphan docs)."""
import os
import re
import unittest

from tests.daemon_helpers import ROOT

DOCS = os.path.join(ROOT, "docs")
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def _md_files():
    out = [os.path.join(ROOT, "README.md")]
    for base, _dirs, files in os.walk(DOCS):
        out.extend(os.path.join(base, f) for f in files if f.endswith(".md"))
    return sorted(out)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _strip_code(text):
    """Drop fenced blocks and inline code spans so example links are not checked."""
    lines, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(lines)


def _slug(heading):
    s = re.sub(r"`", "", heading.strip().lower())
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _anchors(path):
    found, counts, fenced = set(), {}, False
    for line in _read(path).splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            base = _slug(m.group(1))
            n = counts.get(base, 0)
            counts[base] = n + 1
            found.add(base if n == 0 else "%s-%d" % (base, n))
    return found


def _links(path):
    for target in LINK.findall(_strip_code(_read(path))):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):  # http:, https:, mailto:
            continue
        yield target


class DocsLinks(unittest.TestCase):
    def test_every_relative_link_resolves(self):
        problems = []
        for path in _md_files():
            rel = os.path.relpath(path, ROOT)
            for target in _links(path):
                file_part, _, frag = target.partition("#")
                dest = path if not file_part else os.path.normpath(
                    os.path.join(os.path.dirname(path), file_part))
                if not os.path.exists(dest):
                    problems.append("%s: broken link %s" % (rel, target))
                elif frag and dest.endswith(".md") and frag not in _anchors(dest):
                    problems.append("%s: missing anchor %s" % (rel, target))
        self.assertEqual(problems, [])

    def test_no_orphan_docs(self):
        home = os.path.join(DOCS, "README.md")
        self.assertTrue(os.path.exists(home), "docs/README.md is the docs home")
        linked = set()
        for target in _links(home):
            file_part = target.partition("#")[0]
            if file_part:
                linked.add(os.path.normpath(os.path.join(DOCS, file_part)))
        orphans = [os.path.relpath(p, ROOT) for p in _md_files()
                   if p not in (home, os.path.join(ROOT, "README.md")) and p.startswith(DOCS + os.sep)
                   and p not in linked]
        self.assertEqual(orphans, [], "linked from no entry in docs/README.md")

    def test_readme_points_at_the_docs_home(self):
        self.assertIn("(docs/README.md)", _read(os.path.join(ROOT, "README.md")))


if __name__ == "__main__":
    unittest.main()
