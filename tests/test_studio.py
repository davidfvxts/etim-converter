"""Upload und Laufsteuerung des Cockpits.

Hochgeladene Kataloge sind Kundendaten. Geprüft wird darum nicht nur, dass der
Weg funktioniert, sondern auch, dass er die Zusagen hält: nur PDF/XLSX/CSV,
am Inhalt erkannt; kein Überschreiben bestehender Jobs; kein zweiter Lauf auf
demselben Job; kein Ausbruch aus out/.
"""
import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from etim import config, studio, ui

FIX = Path(__file__).parent / "fixtures"

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n"
CSV = FIX.joinpath("katalog_mini.csv").read_bytes()


def xlsx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


# ------------------------------------------------------------------ Dateityp

def test_sniff_accepts_the_three_formats():
    assert studio.sniff(PDF, "katalog.pdf") == ".pdf"
    assert studio.sniff(xlsx_bytes(), "preise.xlsx") == ".xlsx"
    assert studio.sniff(CSV, "katalog_mini.csv") == ".csv"


def test_sniff_judges_content_not_extension():
    """Eine umbenannte Datei kommt nicht durch."""
    with pytest.raises(studio.UploadError):
        studio.sniff(b"<html><body>kein Katalog</body></html>", "katalog.pdf")
    with pytest.raises(studio.UploadError):
        studio.sniff(b"MZ\x90\x00binary", "katalog.csv")
    with pytest.raises(studio.UploadError, match="Excel"):
        studio.sniff(_plain_zip(), "mappe.xlsx")
    # PDF-Inhalt unter falscher Endung wird am Inhalt erkannt und angenommen
    assert studio.sniff(PDF, "katalog.txt") == ".pdf"


def _plain_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("notizen.txt", "kein Excel")
    return buf.getvalue()


def test_sniff_rejects_csv_without_delimiter():
    with pytest.raises(studio.UploadError, match="Trennzeichen"):
        studio.sniff(b"nur eine zeile ohne trenner\nund noch eine", "x.csv")


# ------------------------------------------------------------------ Jobnamen

def test_slug_from_filename():
    assert studio.slug("Preisliste2025_Wärmegruppen.pdf") == "preisliste2025-w-rmegruppen"
    assert studio.slug("../../etc/passwd") == "passwd"
    assert studio.slug("....") == "katalog"


def test_safe_job_blocks_traversal():
    assert studio.safe_job("strawa") == "strawa"
    for bad in ("../etc", "a/b", "", ".", "..", "/abs", "x" * 80):
        with pytest.raises(studio.UploadError):
            studio.safe_job(bad)


def test_upload_never_overwrites(tmp_path):
    a = studio.accept_upload(tmp_path, "katalog.csv", CSV)
    b = studio.accept_upload(tmp_path, "katalog.csv", CSV)
    c = studio.accept_upload(tmp_path, "katalog.csv", CSV)
    assert [a["job"], b["job"], c["job"]] == ["katalog", "katalog-2", "katalog-3"]
    assert (tmp_path / "katalog" / "source" / "katalog.csv").read_bytes() == CSV
    meta = json.loads((tmp_path / "katalog" / "job.json").read_text())
    assert meta["original_filename"] == "katalog.csv" and meta["kind"] == "CSV"


def test_upload_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    big = PDF + b"0" * (2 * 1024 * 1024)
    with pytest.raises(studio.UploadError, match="MB"):
        studio.accept_upload(tmp_path, "gross.pdf", big)
    assert not list(tmp_path.iterdir()), "abgelehnte Uploads legen keinen Job an"


def test_upload_rejects_empty(tmp_path):
    with pytest.raises(studio.UploadError, match="leer"):
        studio.accept_upload(tmp_path, "leer.pdf", b"")


def test_second_run_on_same_job_is_refused(tmp_path):
    """Solange ein Lauf aktiv ist, startet auf demselben Job kein zweiter."""
    job_dir = tmp_path / "busy"
    job_dir.mkdir()
    studio.RUNS["busy"] = studio.RunState(job="busy", kind="compare", state="running")
    try:
        with pytest.raises(studio.UploadError, match="bereits ein Lauf"):
            studio.start(job_dir, kind="compare")
    finally:
        studio.RUNS.pop("busy", None)


# ---------------------------------------------------------------- HTTP-Pfad

@pytest.fixture
def server(tmp_path):
    handler = type("T", (ui.Handler,), {"out_root": tmp_path, "default_job": ""})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()
    httpd.server_close()


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_http_upload_and_run(server, model):
    base, out_root = server

    status, created = call(f"{base}/api/upload", CSV,
                           {"X-Filename": "katalog_mini.csv", "Content-Type": "application/octet-stream"})
    assert status == 201 and created["job"] == "katalog-mini"
    assert (out_root / "katalog-mini" / "source" / "katalog.csv").exists()

    # zweiter Upload desselben Namens legt einen neuen Job an
    status, second = call(f"{base}/api/upload", CSV, {"X-Filename": "katalog_mini.csv"})
    assert status == 201 and second["job"] == "katalog-mini-2"

    # falscher Inhalt wird mit lesbarer Begründung abgelehnt
    status, err = call(f"{base}/api/upload", b"<html>nope</html>", {"X-Filename": "katalog.pdf"})
    assert status == 400 and "PDF" in err["error"]

    status, jobs = call(f"{base}/api/jobs")
    assert status == 200 and {j["job"] for j in jobs["jobs"]} == {"katalog-mini", "katalog-mini-2"}
    assert jobs["jev"]["simulated"] is True

    # kompletter Lauf: einlesen, Retrieval, beide Modelle
    status, run = call(f"{base}/api/run", json.dumps({"job": "katalog-mini", "kind": "full"}).encode(),
                       {"Content-Type": "application/json"})
    assert status == 202 and run["state"] == "running"
    assert [s["key"] for s in run["stages"] if s["active"]] == ["ingest", "retrieval", "gemini", "jev"]

    for _ in range(200):
        _, st = call(f"{base}/api/run?job=katalog-mini")
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st.get("error")

    comp = json.loads((out_root / "katalog-mini" / "compare.json").read_text())
    assert len(comp["items"]) == 4
    status, payload = call(f"{base}/api/job?job=katalog-mini")
    assert status == 200 and payload["compare"]["metrics"]["jev"]["simulated"] is True


def test_http_decodes_umlauts_in_filename(server, model):
    """Der Browser schickt den Dateinamen prozentkodiert — der Jobname darf das nicht zeigen."""
    from urllib.parse import quote

    base, out_root = server
    status, created = call(f"{base}/api/upload", CSV,
                           {"X-Filename": quote("Preisliste2025_Wärmegruppen.csv")})
    assert status == 201 and created["job"] == "preisliste2025-w-rmegruppen"
    meta = json.loads((out_root / created["job"] / "job.json").read_text())
    assert meta["original_filename"] == "Preisliste2025_Wärmegruppen.csv"


def test_http_oversize_upload_is_refused_cleanly(server, model, monkeypatch):
    """Zu große Datei: Ablehnung mit Begründung, kein Job, keine hängende Verbindung."""
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    status, err = call(f"{base_of(server)}/api/upload", PDF + b"0" * (2 * 1024 * 1024),
                       {"X-Filename": "gross.pdf"})
    assert status == 400 and "MB" in err["error"]
    assert not list(server[1].iterdir()), "abgelehnte Uploads legen keinen Job an"
    # die nächste Anfrage muss trotzdem sauber beantwortet werden
    assert call(f"{base_of(server)}/api/jobs")[0] == 200


def base_of(server):
    return server[0]


def test_http_rejects_unknown_job_and_traversal(server, model):
    base, _ = server
    assert call(f"{base}/api/job?job=gibtsnicht")[0] == 404
    assert call(f"{base}/api/job?job=../../etc")[0] == 400
    status, err = call(f"{base}/api/run", json.dumps({"job": "gibtsnicht"}).encode(),
                       {"Content-Type": "application/json"})
    assert status == 404


def test_http_run_needs_products(server, model):
    """Ein Vergleich ohne eingelesene Artikel wird erklärt, nicht versucht."""
    base, out_root = server
    (out_root / "leer").mkdir()
    status, err = call(f"{base}/api/run", json.dumps({"job": "leer", "kind": "compare"}).encode(),
                       {"Content-Type": "application/json"})
    assert status == 400 and "products.json" in err["error"]


def test_http_blocks_foreign_origin(server, model):
    """Eine fremde Seite darf den lokalen Server nicht fernsteuern."""
    base, _ = server
    status, err = call(f"{base}/api/upload", CSV,
                       {"X-Filename": "k.csv", "Origin": "https://example.com"})
    assert status == 403


def test_decisions_are_written_per_job(server, model):
    base, out_root = server
    call(f"{base}/api/upload", CSV, {"X-Filename": "deko.csv"})
    body = json.dumps({"RS-2025-M8": {"status": "ok"}}).encode()
    status, res = call(f"{base}/api/decisions?job=deko", body, {"Content-Type": "application/json"})
    assert status == 200 and res["saved"] == 1
    saved = json.loads((out_root / "deko" / "review.decisions.json").read_text())
    assert saved["RS-2025-M8"]["status"] == "ok"
