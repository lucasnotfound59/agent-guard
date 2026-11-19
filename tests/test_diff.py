from agent_guard.diff import parse_apply_patch, parse_unified, staged, working
from tests.conftest import git

SAMPLE = """diff --git a/src/a.py b/src/a.py
index 111..222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -3,0 +4,2 @@ def f():
+    x = 1
+    y = 2
@@ -10 +12 @@
-old
+new
diff --git a/old.txt b/new.txt
similarity index 100%
rename from old.txt
rename to new.txt
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/img.png b/img.png
new file mode 100644
Binary files /dev/null and b/img.png differ
"""


def test_parse_unified():
    files = {f.path: f for f in parse_unified(SAMPLE)}
    assert files["src/a.py"].added == [(4, "    x = 1"), (5, "    y = 2"), (12, "new")]
    assert files["new.txt"].status == "R" and files["new.txt"].old_path == "old.txt"
    assert files["gone.py"].status == "D"
    assert files["img.png"].binary


def test_apply_patch():
    patch = """*** Begin Patch
*** Add File: src/new.ts
+export const x: any = 1;
*** Update File: src/old.ts
@@
-const a = 1;
+const a = 2;
*** Move to: src/renamed.ts
*** Delete File: src/dead.ts
*** End Patch"""
    paths, added = parse_apply_patch(patch)
    assert paths == ["src/new.ts", "src/renamed.ts"]
    assert added["src/new.ts"] == ["export const x: any = 1;"]
    assert added["src/renamed.ts"] == ["const a = 2;"]


def test_working_and_staged(repo):
    (repo / "README.md").write_text("hello\nworld\n")
    (repo / "new.py").write_text("print(1)\n")
    files = {f.path: f for f in working(repo)}
    assert files["README.md"].added == [(2, "world")]
    assert files["new.py"].added == [(1, "print(1)")]
    assert working(repo, ["new.py"])[0].path == "new.py"
    git(repo, "add", "new.py")
    assert [f.path for f in staged(repo)] == ["new.py"]


def test_working_without_head(tmp_path):
    r = tmp_path / "fresh"
    r.mkdir()
    git(r, "init", "-q")
    (r / "a.py").write_text("x = 1\n")
    git(r, "add", "a.py")
    (r / "b.py").write_text("y = 2\n")
    assert {f.path for f in working(r)} == {"a.py", "b.py"}
