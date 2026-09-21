"""Der Jev-Client: Anfrageform, Transport und Fehlerbilder.

Die Anfrageform ist hier festgenagelt, weil sie gegen eine fremde API läuft:
wenn sich an `state`/`questions`, am Modellnamen oder am Auspacken der Antwort
etwas verschiebt, soll das ein Test sagen und nicht ein Kundenlauf.
"""
import pytest

from etim import config, jev

STATE = {"article_name": "Kugelhahn DN 20"}
QUESTIONS = {"etim_class": {"type": "choice", "instructions": "?", "criteria": {"EC000003": "Ball valve"}}}

CHOICE_ANSWER = {
    "model": "jev-1.13.0",
    "answers": {"etim_class": {"type": "choice", "choice": "EC000003", "confidence": 0.81,
                               "probabilities": {"EC000003": 0.88, "EC000001": 0.12}},
                "is_accessory": {"type": "noul", "noul": 0.07}},
    "usage": {"input_tokens": 318, "output_tokens": 34},
}


@pytest.fixture
def live(monkeypatch):
    """Kein Trockenlauf — der echte Aufrufpfad, nur ohne Netz."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    sent = {}

    def fake_post(url, body, headers, timeout):
        sent.update(url=url, body=body, headers=headers, timeout=timeout)
        return dict(CHOICE_ANSWER)

    monkeypatch.setattr(jev, "_post", fake_post)
    return sent


def test_worker_transport_sends_bare_payload(live, monkeypatch):
    """Über den eigenen Worker geht genau {state, questions} — das Modell steht dort."""
    monkeypatch.setattr(config, "JEV_TRANSPORT", "worker")
    monkeypatch.setattr(config, "JEV_WORKER_URL", "https://worker.example/jev")
    monkeypatch.setattr(config, "JEV_WORKER_SECRET", "s3cr3t")

    res = jev.ask("classify", STATE, QUESTIONS)

    assert live["url"] == "https://worker.example/jev"
    assert live["body"] == {"state": STATE, "questions": QUESTIONS}
    assert live["headers"]["Authorization"] == "Bearer s3cr3t"
    assert res.model == "jev-1.13.0" and not res.simulated
    assert res.choice_of("etim_class")[0] == "EC000003"
    assert res.noul_of("is_accessory") == 0.07


def test_cloudflare_transport_wraps_input(live, monkeypatch):
    """Direkt an Workers AI: Modellname im Rumpf, Antwort steckt unter 'result'."""
    monkeypatch.setattr(config, "JEV_TRANSPORT", "cloudflare")
    monkeypatch.setattr(config, "CF_ACCOUNT_ID", "acc123")
    monkeypatch.setattr(config, "CF_API_TOKEN", "tok")
    monkeypatch.setattr(jev, "_post", lambda *a, **k: {"success": True, "result": CHOICE_ANSWER})

    res = jev.ask("classify", STATE, QUESTIONS)
    assert res.choice_of("etim_class")[0] == "EC000003"

    # und die URL/der Rumpf stimmen
    monkeypatch.setattr(jev, "_post", lambda url, body, headers, timeout:
                        live.update(url=url, body=body, headers=headers) or CHOICE_ANSWER)
    jev.ask("classify", STATE, QUESTIONS)
    assert live["url"] == "https://api.cloudflare.com/client/v4/accounts/acc123/ai/run"
    assert live["body"]["model"] == "typesafe/jev"
    assert live["body"]["input"] == {"state": STATE, "questions": QUESTIONS}


def test_missing_credentials_are_named(monkeypatch):
    """Ohne Zugang gibt es eine Anleitung, keinen Stacktrace — und kein Ergebnis."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "JEV_TRANSPORT", "worker")
    monkeypatch.setattr(config, "JEV_WORKER_URL", "")
    ok, reason = jev.configured()
    assert not ok and "ETIM_JEV_WORKER_URL" in reason
    with pytest.raises(jev.JevError, match="ETIM_JEV_WORKER_URL"):
        jev.ask("classify", STATE, QUESTIONS)

    monkeypatch.setattr(config, "JEV_WORKER_URL", "https://w/jev")
    monkeypatch.setattr(config, "JEV_WORKER_SECRET", "")
    ok, reason = jev.configured()
    assert not ok and "SECRET" in reason


def test_status_marks_dry_run_as_simulation(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    st = jev.status()
    assert st["ready"] and st["simulated"] and "simuliert" in st["reason"]


def test_http_errors_become_readable_german(live, monkeypatch):
    import urllib.error

    monkeypatch.setattr(config, "JEV_TRANSPORT", "worker")
    monkeypatch.setattr(config, "JEV_WORKER_URL", "https://w/jev")
    monkeypatch.setattr(config, "JEV_WORKER_SECRET", "s")

    def raise_401(*a, **k):
        raise urllib.error.HTTPError("https://w/jev", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(jev, "_post", raise_401)
    with pytest.raises(jev.JevError, match="401"):
        jev.ask("classify", STATE, QUESTIONS)


def test_answer_without_answers_key_is_an_error(live, monkeypatch):
    """Eine Antwort, die keine ist, darf nicht als leeres Ergebnis durchgehen."""
    monkeypatch.setattr(config, "JEV_TRANSPORT", "worker")
    monkeypatch.setattr(config, "JEV_WORKER_URL", "https://w/jev")
    monkeypatch.setattr(config, "JEV_WORKER_SECRET", "s")
    monkeypatch.setattr(jev, "_post", lambda *a, **k: {"error": "kaputt"})
    with pytest.raises(jev.JevError, match="answers"):
        jev.ask("classify", STATE, QUESTIONS)


def test_cost_counts_only_input_tokens():
    """Jev berechnet nur Input; Output ist kostenlos."""
    r = jev.JevResult(answers={}, usage={"input_tokens": 1_000_000, "output_tokens": 999_999})
    assert r.cost_usd == pytest.approx(config.JEV_INPUT_USD_PER_M)


def test_question_builders_guard_the_limits():
    assert jev.noul("x")["type"] == "noul"
    assert "criteria" not in jev.noul("x"), "ohne Beschreibungen bleibt criteria weg"
    assert jev.noul("x", "ja", "nein")["criteria"] == {"true": "ja", "false": "nein"}
    assert jev.score("x", ["a", "b"])["criteria"] == ["a", "b"]
    with pytest.raises(ValueError):
        jev.score("x", ["nur eine"])
    with pytest.raises(ValueError):
        jev.score("x", [str(i) for i in range(11)])
    with pytest.raises(ValueError):
        jev.choice("x", {})


def test_error_text_never_leaks_the_secret(monkeypatch):
    """Ein fremder Fehlertext könnte das mitgeschickte Secret enthalten."""
    monkeypatch.setattr(config, "JEV_WORKER_SECRET", "supergeheim-12345678")
    monkeypatch.setattr(config, "CF_API_TOKEN", "")
    text = jev._explain(500, "upstream said: Bearer supergeheim-12345678 rejected")
    assert "supergeheim" not in text and "<secret>" in text
