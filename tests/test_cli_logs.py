import json
import logging

import pytest

from conciliacion import cli
from conciliacion.ingest.sharepoint_sync import ErrorAutenticacionGraph, GraphClient


class _Resp:
    def __init__(self, status: int, cuerpo: dict):
        self.status_code, self._cuerpo = status, cuerpo
        self.ok = status < 400
        self.text = json.dumps(cuerpo)

    def json(self):
        return self._cuerpo


class _SesionToken:
    def __init__(self, resp):
        self.resp = resp

    def post(self, *a, **kw):
        return self.resp


def test_token_rechazado_da_mensaje_legible():
    resp = _Resp(
        400,
        {
            "error": "invalid_request",
            "error_description": "AADSTS90002: Tenant '0000' not found.\nTrace ID: x",
        },
    )
    g = GraphClient("0000", "cid", "s3cr3t-valor", session=_SesionToken(resp))
    with pytest.raises(ErrorAutenticacionGraph) as e:
        g.token()
    msg = str(e.value)
    assert "HTTP 400" in msg and "invalid_request" in msg and "AADSTS90002" in msg
    assert "Trace ID" not in msg and "s3cr3t-valor" not in msg


def test_error_se_registra_en_una_sola_linea_json(monkeypatch, capsys):
    def explota(args):
        raise ErrorAutenticacionGraph('Microsoft rechazó "x"')

    monkeypatch.setattr(cli, "cmd_fuentes", explota)
    assert cli.main(["fuentes"]) == 1
    lineas = [x for x in capsys.readouterr().err.splitlines() if x.strip()]
    assert len(lineas) == 1
    evento = json.loads(lineas[0])
    assert evento["severity"] == "ERROR"
    assert "ErrorAutenticacionGraph" in evento["error"]
    assert "Traceback" in evento["traceback"]
    logging.getLogger().handlers.clear()
