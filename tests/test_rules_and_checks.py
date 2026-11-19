import pytest

from agent_guard.checks import active, check_command, check_commit_message, check_files
from agent_guard.diff import FileDiff
from agent_guard.rules import active_rules, global_rules, path_matches, validate


def rules_by_id():
    return {r.id: r for r in global_rules("active") + global_rules("candidate")}


def flagged(rule_id: str, path: str, line: str) -> bool:
    rule = rules_by_id()[rule_id]
    fd = FileDiff(path=path, added=[(1, line)])
    return bool(active(check_files([rule], [fd])))


def test_every_shipped_rule_is_valid():
    for status in ("active", "candidate"):
        for r in global_rules(status):
            assert validate(r) == [], r.id


@pytest.mark.parametrize("path,patterns,expected", [
    ("src/a.ts", ["*.ts"], True),
    ("src/a.d.ts", ["*.ts"], True),
    ("src/a.tsx", ["*.ts"], False),
    ("TeamCode/src/main/java/X.java", ["**/TeamCode/**/*.java"], True),
    ("a/TeamCode/X.java", ["**/TeamCode/**/*.java"], True),
    ("rules/active/x.yaml", ["rules/active/*.yaml"], True),
    ("rules/active/sub/x.yaml", ["rules/active/*.yaml"], False),
    ("./.env", [".env"], True),
])
def test_globs(path, patterns, expected):
    assert path_matches(path, patterns) is expected


@pytest.mark.parametrize("rule,path,line,expected", [
    ("no-secrets", "app.py", 'client = OpenAI(api_key="sk-proj-abcdefghijklmnopqrstuvwx")', True),
    ("no-secrets", "app.py", "AWS = 'AKIA" "ABCDEFGHIJKLMNOP'", True),
    ("no-secrets", "app.py", 'password = os.environ["DB_PASSWORD"]', False),
    ("no-secrets", ".env.example", 'API_KEY="sk-proj-abcdefghijklmnopqrstuvwx"', False),
    ("no-hardcoded-home-path", "x.py", 'open("/Users/someone/data.csv")', True),
    ("no-hardcoded-home-path", "x.py", 'open(Path.home() / "data.csv")', False),
    ("no-hardcoded-home-path", "notes.md", "see /Users/someone/notes", False),
    ("no-ts-any", "a.ts", "function f(x: any) {}", True),
    ("no-ts-any", "a.ts", "const y = x as any;", True),
    ("no-ts-any", "a.ts", "const anyway = company;", False),
    ("no-ts-any", "a.d.ts", "declare const x: any;", False),
    ("no-placeholder-stubs", "a.py", "# TODO: handle errors", True),
    ("no-placeholder-stubs", "a.py", "return 0  # simplified version for now", True),
    ("no-placeholder-stubs", "a.tsx", '<input placeholder="Email" />', False),
    ("no-placeholder-stubs", "a.ts", "const todoList = [];", False),
    ("no-conflict-markers", "a.py", "<<<<<<< HEAD", True),
    ("no-conflict-markers", "a.md", "=======", False),
    ("no-focused-tests", "a.test.ts", "it.only('works', () => {})", True),
    ("no-focused-tests", "a.test.ts", "it('works', () => {})", False),
    ("no-check-suppression", "a.ts", "// @ts-ignore", True),
    ("no-check-suppression", "a.ts", "// @ts-expect-error explained", False),
    ("no-skipped-tests", "a.test.ts", "describe.skip('x', () => {})", True),
    ("no-silenced-errors", "a.js", "try { run() } catch (e) {}", True),
    ("no-silenced-errors", "a.py", "except Exception: pass", True),
    ("no-silenced-errors", "a.py", "except ValueError as e: log(e)", False),
    ("ftc-no-thread-sleep", "TeamCode/src/main/java/org/x/Auto.java", "Thread.sleep(500);", True),
    ("ftc-no-thread-sleep", "src/main/java/Other.java", "Thread.sleep(500);", False),
    ("no-skipped-tests", "TeamCode/src/main/java/org/x/Tuner.java", "@Disabled", False),
    ("no-skipped-tests", "app/src/test/java/FooTest.java", "@Disabled", True),
    ("no-placeholder-stubs", "a.tsx", "placeholder?: string;", False),
    ("no-placeholder-stubs", "a.py", "return 42  # placeholder value", True),
])
def test_added_line_rules(rule, path, line, expected):
    assert flagged(rule, path, line) is expected


def test_multiline_empty_catch():
    rule = rules_by_id()["no-silenced-errors"]
    fd = FileDiff(path="a.ts", added=[(1, "try {"), (2, "  run();"), (3, "} catch (err) {"), (4, "}")])
    assert check_files([rule], [fd])


def test_allow_comment_suppresses_with_reason():
    rule = rules_by_id()["no-ts-any"]
    fd = FileDiff(path="a.ts", added=[(1, "cb(x as any) // agent-guard: allow no-ts-any lib has no types")])
    found = check_files([rule], [fd])
    assert found and found[0].suppressed == "lib has no types" and not active(found)


@pytest.mark.parametrize("cmd,rule,expected", [
    ("git commit --no-verify -m 'x'", "no-hook-bypass", True),
    ("git commit -nm 'fix: x'", "no-hook-bypass", True),
    ("git config --global core.hooksPath /tmp/x", "no-hook-bypass", True),
    ("git commit -m 'fix: x'", "no-hook-bypass", False),
    ("git push --force origin main", "no-destructive-git", True),
    ("git push -f", "no-destructive-git", True),
    ("git push --force-with-lease", "no-destructive-git", False),
    ("git reset --hard HEAD~1", "no-destructive-git", True),
    ("git checkout -- .", "no-destructive-git", True),
    ("git checkout main", "no-destructive-git", False),
    ("git clean -fdx", "no-destructive-git", True),
    ("rm -rf ~", "no-dangerous-rm", True),
    ("rm -rf /", "no-dangerous-rm", True),
    ("rm -rf $HOME/", "no-dangerous-rm", True),
    ("cd x && rm -rf * && ls", "no-dangerous-rm", True),
    ("rm -rf build/", "no-dangerous-rm", False),
    ("rm -rf ./node_modules", "no-dangerous-rm", False),
])
def test_command_rules(cmd, rule, expected):
    r = rules_by_id()[rule]
    assert bool(check_command([r], cmd)) is expected


@pytest.mark.parametrize("msg,expected_warn", [
    ("feat(auth): add login rate limit", False),
    ("fix: handle empty input\n\nbody", False),
    ("patch: bump version", False),
    ("rebase: squash wip commits", False),
    ("refactor!: drop python 3.9", False),
    ("update stuff", True),
    ("Fixed the bug", True),
    ("Merge branch 'main' into feature", False),
    ("fixup! feat: add x", False),
    ("# comment only\n", False),
])
def test_conventional_commits(msg, expected_warn):
    r = rules_by_id()["conventional-commits"]
    assert bool(check_commit_message([r], msg)) is expected_warn


def test_env_files_path_rule():
    r = rules_by_id()["no-env-files"]
    assert check_files([r], [FileDiff(path="config/.env", status="A")])
    assert check_files([r], [FileDiff(path="local.properties", status="A")])
    assert not check_files([r], [FileDiff(path=".env.example", status="A")])


def test_diff_size():
    r = rules_by_id()["large-commit"]
    many = [FileDiff(path=f"f{i}.py", added=[(1, "x")]) for i in range(31)]
    assert check_files([r], many)
    assert not check_files([r], many, include_size=False)
    assert not check_files([r], many[:5])


def test_project_overlay(repo):
    (repo / ".agent-guard.yaml").write_text(
        "disable:\n  - id: no-ts-any\n    reason: generated client\n  - id: no-placeholder-stubs\n"
        "rules:\n  - id: no-foo\n    title: No foo\n    severity: soft\n"
        "    check: {kind: added-line-forbidden, pattern: 'foo'}\n    why: because\n    fix: use bar\n"
    )
    rules, project = active_rules(repo)
    ids = {r.id for r in rules}
    assert "no-ts-any" not in ids  # disabled with a reason
    assert "no-placeholder-stubs" in ids  # disable without reason is ignored
    assert "no-foo" in ids
    assert any("no reason" in p for p in project.problems)
