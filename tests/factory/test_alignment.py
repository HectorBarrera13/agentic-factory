"""Alineación con el instructivo: reanudación tras ambigüedad, re-review, severidad,
artefactos §17, traceability sin relleno, contexto seleccionado y tools acotadas.

Solo FakeExecutor / repos temporales: no requiere API key ni toca runs/ reales.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from factory_app import orchestrator as ORCH
from factory_app import tools
from factory_app.orchestrator import AwaitingHuman, FakeExecutor
from tests.factory.conftest import activate_project
from tests.factory.test_orchestrator import (
    CR_AMBIGUOUS,
    approve,
    decide,
    drive,
    sandbox,  # noqa: F401  (fixture)
    state,
)

LATER = "9999-12-31T00:00:00+00:00"


def human(run, gate, decision="APPROVED", reason="aclaracion del PO", recorded_at=LATER):
    (run / f"human-{gate}-decision.json").write_text(
        json.dumps(
            {
                "decision": decision,
                "reason": reason,
                "recorded_by": "Ana (Product Owner)",
                "recorded_at": recorded_at,
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- #5 reanudar ESCALATED
def test_escalated_ambiguity_resumes_after_po_decision(sandbox):  # noqa: F811
    run = sandbox(cr_text=CR_AMBIGUOUS)
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", FakeExecutor())
    assert state(run)["status"] == "ESCALATED"
    human(run, "requirements")  # el PO decide la politica DESPUES del escalamiento
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    # la aclaracion solo reanuda: el intake regenerado exige una aprobacion NUEVA del PO
    assert exc.value.gate == "requirements"
    assert "1. [requirements" in state(run)["po_clarification"]
    assert "aclaracion del PO" in state(run)["po_clarification"]
    assert [c["reason"] for c in state(run)["po_clarifications"]] == ["aclaracion del PO"]
    assert (run / "human-requirements-decision-clarification.json").is_file()
    assert not (run / "03-implementation-plan.md").exists()
    approve(run, "requirements")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == "plan"
    assert (run / "03-implementation-plan.md").is_file()


class PlannerAsksOnce(FakeExecutor):
    def __init__(self):
        super().__init__()
        self.asked = False

    def run_agent(self, phase, context):
        if phase == "plan" and not self.asked:
            self.asked = True
            return {"report": "# Plan\n", "recommendation": "HUMAN_CLARIFICATION_REQUIRED"}
        return super().run_agent(phase, context)


def test_planner_clarification_requires_fresh_plan_approval(sandbox):  # noqa: F811
    run = sandbox()
    ex = PlannerAsksOnce()
    assert drive(run, ex, stop_at="plan").gate == "plan"  # el planner pide aclaracion
    assert state(run)["status"] == "ESCALATED"
    approve(run, "plan")  # aclaracion del Tech Lead
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", ex)
    assert exc.value.gate == "plan"  # el plan NUEVO espera su propia aprobacion
    assert not (run / "05-coder-report.md").exists()
    approve(run, "plan")
    assert drive(run, ex, stop_at="review").gate == "review"


def test_escalated_does_not_resume_without_fresh_decision(sandbox):  # noqa: F811
    run = sandbox(cr_text=CR_AMBIGUOUS)
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", FakeExecutor())
    # decision sin recorded_at (no demostrable como posterior) => sigue detenido
    human(run, "requirements", recorded_at="")
    assert ORCH.advance("CR-T", FakeExecutor())["status"] == "ESCALATED"
    assert not (run / "03-implementation-plan.md").exists()


def test_rejected_gate_never_resumes(sandbox):  # noqa: F811
    run = sandbox()
    drive(run, FakeExecutor(), decisions={"requirements": "REJECTED"})
    assert state(run)["status"] == "ESCALATED"
    human(run, "requirements", decision="APPROVED")  # aprobar despues NO reabre un rechazo
    assert ORCH.advance("CR-T", FakeExecutor())["status"] == "ESCALATED"


# ---------------------------------------------------------------- #7 re-review
class CountingExecutor(FakeExecutor):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.review_runs = 0

    def run_agent(self, phase, context):
        self.review_runs += phase == "review"
        return super().run_agent(phase, context)


def test_review_is_not_rerun_when_resuming_after_human_decision(sandbox):  # noqa: F811
    run = sandbox()
    ex = CountingExecutor()
    # el gate de review se alcanza SIN decision: se registra despues
    drive(run, ex, stop_at="review")
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", ex)  # reanudar sin decision tampoco regenera
    assert ex.review_runs == 1
    approve(run, "review")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", ex)
    assert exc.value.gate == "release"
    assert ex.review_runs == 1  # el humano clasifico ESTOS findings; no se regeneran


def test_fix_round_archives_previous_reports_and_writes_re_review(sandbox):  # noqa: F811
    run = sandbox()
    ex = FakeExecutor(review_report="# Review\n\nRF-01 | MUST FIX | race\nMUST FIX: 1\n")
    drive(run, ex, stop_at="review")
    decide(run, RF_01="ACCEPTED")
    approve(run, "review")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", ex)
    assert exc.value.gate == "review"
    assert "RF-01" in (run / "08-review-report-round0.md").read_text()  # ronda 0 conservada
    assert "RF-01" not in (run / "08-review-report.md").read_text()
    assert (run / "09-security-report-round0.md").is_file()
    assert (run / "13-re-review.md").is_file()
    assert (run / "12-re-verification.txt").is_file()


# ---------------------------------------------------------------- #18 severidad
def test_accepted_should_fix_does_not_trigger_fix_cycle(sandbox):  # noqa: F811
    run = sandbox()
    ex = FakeExecutor(review_report="# Review\n\nRF-02 | SHOULD FIX | naming\nSHOULD FIX: 1\n")
    exc = drive(run, ex, findings={"RF_02": "ACCEPTED"}, stop_at="release")
    assert exc.gate == "release"
    assert not (run / "11-fix-report.md").exists()


def test_severity_parsing_same_line_before_segment():
    text = "MUST FIX: 1 (RF-01)\nSHOULD FIX: 0\n| RF-02 | SHOULD FIX | x |\n### SEC-01\nHIGH sql\n"
    seg = ORCH.finding_segments(text)
    assert set(seg) == {"RF-01", "RF-02", "SEC-01"}


REVIEW_E2E = (
    "# Review CR-05\n\n"
    "RF-01 | SHOULD FIX | AC-03 | js/app.js | no se llama a resolveConflicts\n"
    "  la marca 'en conflicto' nunca llega al navegador\n\n"
    "MUST FIX: 0\nSHOULD FIX: 1\n"
)
SECURITY_E2E = (
    "# Security CR-05\n\nHIGH: 0\nMEDIUM: 0\n\n"
    "## Hallazgos HIGH de escaner\nninguno\n\n```json\n{\"semgrep\": []}\n```\n"
)


def test_a_finding_excerpt_stops_at_the_end_of_its_own_report(tmp_path):
    (tmp_path / "08-review-report.md").write_text(REVIEW_E2E, encoding="utf-8")
    (tmp_path / "09-security-report.md").write_text(SECURITY_E2E, encoding="utf-8")
    seg = ORCH.report_segments(tmp_path)["RF-01"]
    assert seg.startswith("RF-01 | SHOULD FIX")
    assert "nunca llega al navegador" in seg  # la linea de evidencia sigue siendo suya
    for leak in ("SHOULD FIX: 1", "# Security", "HIGH: 0", "semgrep"):
        assert leak not in seg
    # la severidad sale de su fila, no del "HIGH: 0" del reporte de seguridad
    assert ORCH.finding_severities(tmp_path) == {"RF-01": "SHOULD FIX"}


def test_a_summary_mention_does_not_steal_the_start_of_the_finding():
    text = "MUST FIX: 1 (RF-01, RF-02)\n\nRF-01 | MUST FIX | race\nRF-02 | SHOULD FIX | nombre\n"
    seg = ORCH.finding_segments(text)
    assert seg["RF-01"] == "RF-01 | MUST FIX | race\n"
    assert seg["RF-02"] == "RF-02 | SHOULD FIX | nombre\n"


def test_table_rows_and_headings_are_finding_rows():
    text = "| RF-01 | MUST FIX | a |\n| RF-02 | SHOULD FIX | b |\n### SEC-01\nHIGH sql\n## Fin\n"
    seg = ORCH.finding_segments(text)
    assert seg["RF-01"] == "| RF-01 | MUST FIX | a |\n"
    assert seg["SEC-01"] == "### SEC-01\nHIGH sql\n"


# ---------------------------------------------------------------- #8 artefactos §17
def test_instructivo_named_artifacts_are_written(sandbox):  # noqa: F811
    run = sandbox()
    ex = FakeExecutor(review_report="# Review\n\nRF-02 | SHOULD FIX | naming\n")
    assert drive(run, ex, findings={"RF_02": "DEFERRED"})["status"] == "READY FOR HUMAN MERGE"
    for name in (
        "02-human-requirement-decision.md",
        "04-human-plan-approval.md",
        "10-human-review-decisions.md",
        "15-release-decision.json",
    ):
        assert (run / name).is_file(), name
    assert json.loads((run / "15-release-decision.json").read_text())["final_status"] == (
        "READY FOR HUMAN MERGE"
    )
    assert "RF-02" in (run / "10-human-review-decisions.md").read_text()


# ---------------------------------------------------------------- #4 traceability real
def test_traceability_marks_unmapped_instead_of_inventing(sandbox):  # noqa: F811
    run = sandbox()
    drive(run, FakeExecutor(), stop_at="release")
    text = (run / "traceability.md").read_text()
    assert "UNMAPPED" in text and "| T1 |" not in text  # el plan falso no cita ningun AC
    run_json = json.loads((run / "factory-run.json").read_text())
    assert run_json["traceability_gaps"] > 0
    assert "Traceability gaps" in (run / "14-evidence-summary.md").read_text()


def test_traceability_links_only_what_evidence_supports(sandbox, monkeypatch):  # noqa: F811
    run = sandbox(cr_text="# CR-T\n\n- AC-CRT.1 algo\n- AC-CRT.2 otra\n")
    root = run.parents[1]
    (root / "tests" / "product").mkdir(parents=True)
    (root / "tests" / "product" / "test_x.py").write_text(
        'def test_waitlist_fifo():\n    """AC-CRT.1"""\n    assert True\n'
    )
    (run / "03-implementation-plan.md").write_text(
        "| TASK-01 | AC-CRT.1 | campusflow/service.py |\n"
    )
    (run / "08-review-report.md").write_text("RF-01 | MUST FIX | AC-CRT.1 | race\n")
    (run / "human-findings-decisions.json").write_text(
        json.dumps({"decisions": [{"finding_id": "RF-01", "decision": "ACCEPTED"}]})
    )
    monkeypatch.setattr(
        ORCH, "_git", lambda args: SimpleNamespace(stdout="campusflow/service.py\n", returncode=0)
    )
    rows = {r["ac"]: r for r in ORCH.compute_traceability(run)}
    one, two = rows["AC-CRT.1"], rows["AC-CRT.2"]
    assert one["tasks"] == ["TASK-01"] and one["code"] == ["campusflow/service.py"]
    assert one["tests"] == ["test_x.py::test_waitlist_fifo"]
    assert one["findings"] == ["RF-01"] and one["decisions"] == ["ACCEPTED"]
    assert two["tasks"] == two["code"] == two["tests"] == [ORCH.UNMAPPED]
    assert ORCH.traceability_gaps(list(rows.values())) == 3


def test_evidence_names_who_signed_each_gate_and_is_rewritten_after_release(sandbox):  # noqa: F811
    run = sandbox()
    drive(run, FakeExecutor(), stop_at="plan")
    (run / "human-plan-decision.json").write_text(json.dumps({
        "decision": "APPROVED", "reason": "ok", "recorded_by": "Tony (Product Owner)",
        "role": "Product Owner",
        "recorded_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
    }), encoding="utf-8")
    drive(run, FakeExecutor())
    run_json = json.loads((run / "factory-run.json").read_text())
    assert run_json["gate_signers"]["plan"] == {
        "by": "Tony (Product Owner)", "role": "Product Owner",
        "at": run_json["gate_signers"]["plan"]["at"]}
    summary = (run / "14-evidence-summary.md").read_text(encoding="utf-8")
    assert "plan: APPROVED por Tony (Product Owner)" in summary
    # tras el release, resumen y trazabilidad muestran el estado final
    trace = (run / "traceability.md").read_text(encoding="utf-8")
    assert "READY FOR HUMAN MERGE" in trace and "AWAITING RELEASE DECISION" not in trace
    assert "release: APPROVED por" in trace


PROSE_PLAN = """# Implementation Plan

### B. Relay Server (`server/relay.mjs`)
- Manages rooms.
- **AC-01 Enforcement**: rejects the 6th connection.

### C. Client UI (`js/app.js`, `index.html`)
- Handles `?room=<id>` on load (AC-02).
- Task 3: mark revisions "en conflicto" (AC-03)
  - edits `sync.js` and renders the badge

## 3. Likely Files
1. **`js/sync.js` (NEW)**: pure logic.
"""


def test_traceability_reads_prose_plans_sections_and_task_names(sandbox, monkeypatch):  # noqa: F811
    run = sandbox(
        cr_text="# CR-T\n\n- AC-01 cupo\n- AC-02 enlace\n- AC-03 conflictos\n- AC-010 x\n"
    )
    p = ORCH.project()
    monkeypatch.setattr(p.config.ids, "acceptance", r"AC-\d+")
    (run / "03-implementation-plan.md").write_text(PROSE_PLAN, encoding="utf-8")
    changed = "server/relay.mjs\njs/app.js\njs/sync.js\nindex.html\nREADME.md\n"
    monkeypatch.setattr(ORCH, "_git", lambda args: SimpleNamespace(stdout=changed, returncode=0))
    # como ada-07: carpetas Y un archivo suelto como areas de codigo
    monkeypatch.setattr(ORCH, "_code_paths", lambda: ["server", "js", "index.html", "tests"])
    rows = {r["ac"]: r for r in ORCH.compute_traceability(run)}
    assert rows["AC-01"]["code"] == ["server/relay.mjs"]  # por el encabezado de su seccion
    assert rows["AC-02"]["code"] == ["index.html", "js/app.js"]
    assert rows["AC-03"]["tasks"] == ["T3"]  # "Task 3"
    assert "js/sync.js" in rows["AC-03"]["code"]  # "sync.js" en su sub-vineta, unico en el diff
    assert rows["AC-010"]["code"] == [ORCH.UNMAPPED]  # AC-01 no es AC-010


def test_node_describe_blocks_cite_their_ac(sandbox, monkeypatch):  # noqa: F811
    run = sandbox(cr_text="# CR-T\n\n- AC-02 enlace\n")
    p = ORCH.project()
    monkeypatch.setattr(p.config.ids, "acceptance", r"AC-\d+")
    tests_dir = p.work_root(run.name) / p.config.code.tests
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_sync.py").write_text(
        'describe("Sync: URL Room Link Parsing (AC-02)", () => {\n'
        '  it("extracts the room", () => {});\n});\n', encoding="utf-8")
    rows = {r["ac"]: r for r in ORCH.compute_traceability(run)}
    assert rows["AC-02"]["tests"] == ["test_sync.py::Sync: URL Room Link Parsing (AC-02)"]


# ---------------------------------------------------------------- tools acotadas
@pytest.fixture()
def tools_sandbox(tmp_path, monkeypatch):
    activate_project(tmp_path, monkeypatch)
    return tmp_path


def test_test_analyst_writes_tests_only(tools_sandbox):
    assert tools.write_test_file("tests/product/test_new.py", "def test_a():\n    pass\n")["ok"]
    for path in (
        "campusflow/service.py",  # codigo de aplicacion
        "tests/product/helper.py",  # no es test_*.py
        "tests/factory/test_x.py",  # no es de producto
        "docs/REQUIREMENTS.md",
        "../outside/test_x.py",
    ):
        assert not tools.write_test_file(path, "x").get("ok"), path
    assert not (tools_sandbox / "campusflow" / "service.py").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git no disponible")
def test_inspect_git_diff_is_read_only_and_shows_branch_changes(tools_sandbox):
    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tools_sandbox,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (tools_sandbox / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "factory/CR-T")
    (tools_sandbox / "a.py").write_text("x = 2\n")
    git("commit", "-qam", "change")
    out = tools.inspect_git_diff(stat_only=False)
    assert out["ok"] and "+x = 2" in out["diff"]
    assert "a.py" in tools.inspect_git_diff(stat_only=True)["diff"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git no disponible")
def test_inspect_git_diff_uses_the_run_base_when_there_is_no_main(tools_sandbox):
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tools_sandbox, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-q", "-b", "trunk")  # sin rama main
    (tools_sandbox / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "factory/CR-T")
    (tools_sandbox / "a.py").write_text("x = 2\n")
    git("commit", "-qam", "change")
    run = tools_sandbox / "runs" / "CR-T"
    run.mkdir(parents=True)
    (run / "factory-state.json").write_text(json.dumps({"base_commit": base}))
    out = tools.inspect_git_diff(stat_only=False)
    assert out["ok"] and "+x = 2" in out["diff"]


# ---------------------------------------------------------------- #9 context engineering
def test_select_context_picks_relevant_sections_and_writes_manifest(tools_sandbox):
    docs = tools_sandbox / "docs"
    docs.mkdir()
    (docs / "REQUIREMENTS.md").write_text(
        "# R\n\n## FR-01 — Events\ncreate\n\n## FR-03 — Registration\nregister\n\n"
        "## FR-04 — Cancellation\ncancel\n\n## NFR-01 — Architecture\nlayers\n",
        encoding="utf-8",
    )
    (docs / "SPEC.md").write_text("# S\n\n## Register\nfull -> error. Covers FR-03.\n")
    (docs / "ARCHITECTURE.md").write_text("# Architecture\nAPI -> Service\n")
    (docs / "REPO_MAP.md").write_text(
        "| Path | Purpose |\n|---|---|\n| `campusflow/service.py` | rules |\n"
        "| `campusflow/api.py` | http |\n"
    )
    (tools_sandbox / "campusflow").mkdir()
    (tools_sandbox / "tests" / "product").mkdir(parents=True)
    (tools_sandbox / "tests" / "product" / "test_service.py").write_text("service AC-X.1\n")
    run = tools_sandbox / "runs" / "CR-T"
    run.mkdir(parents=True)
    (run / "00-change-request.md").write_text(
        "# CR-T\nFR-03 changes: Service must handle full capacity. AC-X.1\n"
    )
    ctx = tools.select_context("CR-T")
    inc, exc = ctx["manifest"]["included"], ctx["manifest"]["excluded"]
    assert "FR-03" in inc["requirements_sections"] and "NFR-01" in inc["requirements_sections"]
    assert "FR-04" in exc["requirements_sections"] and "FR-01" in exc["requirements_sections"]
    assert inc["source_files"] == ["campusflow/service.py"]  # el CR senala Service, no API
    assert "campusflow/api.py" in exc["source_files"]
    assert inc["tests"] == ["tests/product/test_service.py"]
    assert ctx["manifest"]["whole_repo_sent"] is False
    assert "cancel" not in ctx["text"] and "register" in ctx["text"]
    saved = json.loads((run / "context-manifest.json").read_text(encoding="utf-8"))
    assert saved["included"] == inc
