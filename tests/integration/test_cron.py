import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.jobs import cron

pytestmark = pytest.mark.integration


def test_cron_spento_senza_segreto(client):
    assert get_settings().cron_secret is None
    assert client.get("/cron/raccolta-bandi").status_code == 404


def test_cron_rifiuta_segreto_sbagliato_e_job_sconosciuto(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "cron_secret", SecretStr("segreto-di-prova"))
    assert client.get("/cron/raccolta-bandi").status_code == 401
    r = client.get("/cron/raccolta-bandi", headers={"Authorization": "Bearer sbagliato"})
    assert r.status_code == 401
    r = client.get("/cron/inesistente", headers={"Authorization": "Bearer segreto-di-prova"})
    assert r.status_code == 404


def test_cron_esegue_il_job(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "cron_secret", SecretStr("segreto-di-prova"))
    chiamate = []

    def finto_esegui_job(nome, funzione):
        chiamate.append((nome, funzione()))
        return "ok"

    monkeypatch.setattr(cron, "esegui_job", finto_esegui_job)
    monkeypatch.setitem(cron.JOB, "prova", ("prova_cron", lambda: "fatto"))
    r = client.get("/cron/prova", headers={"Authorization": "Bearer segreto-di-prova"})
    assert r.status_code == 200
    assert r.json() == {"job": "prova_cron", "esito": "ok"}
    assert chiamate == [("prova_cron", "fatto")]


def test_cron_conosce_i_job_del_worker_e_il_backup_esterno():
    assert {job for job, _ in cron.JOB.values()} == {
        "raccolta_bandi",
        "raccolta_appalti",
        "documenti_bandi",
        "estrazione_bandi",
        "opportunita",
        "promemoria",
        "pulizia",
        "riferimenti_istat",
        "backup_esterno",
    }
