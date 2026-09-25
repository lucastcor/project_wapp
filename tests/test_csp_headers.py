"""A CSP libera só o que o Embedded Signup da Meta precisa, e nada mais.

O SDK do Facebook é carregado de `connect.facebook.net`, fala com
`facebook.com` por XHR e abre o popup num iframe. Qualquer outra origem
externa em `script-src` ou `connect-src` seria um vetor de exfiltração do
painel, então o cabeçalho é afirmado diretiva por diretiva.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _directives() -> dict[str, str]:
    response = client.get("/platform")
    assert response.status_code == 200, response.text
    header = response.headers["Content-Security-Policy"]
    directives: dict[str, str] = {}
    for part in header.split(";"):
        name, _, value = part.strip().partition(" ")
        if name:
            directives[name] = value.strip()
    return directives


def test_csp_allows_the_meta_sdk_and_nothing_else_external() -> None:
    directives = _directives()

    assert directives["script-src"] == "'self' https://connect.facebook.net"
    assert directives["connect-src"] == (
        "'self' https://www.facebook.com https://web.facebook.com "
        "https://graph.facebook.com https://connect.facebook.net"
    )
    assert directives["frame-src"] == (
        "https://www.facebook.com https://web.facebook.com https://staticxx.facebook.com"
    )


def test_csp_keeps_the_hardened_baseline() -> None:
    directives = _directives()

    assert directives["default-src"] == "'self'"
    assert directives["base-uri"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["form-action"] == "'self'"
    assert directives["img-src"] == "'self' data:"
    assert directives["style-src"] == "'self' 'unsafe-inline' https://fonts.googleapis.com"
    assert directives["font-src"] == "'self' https://fonts.gstatic.com"
    # Nenhuma diretiva liberou tudo: o SDK entra por origem explícita.
    assert "*" not in " ".join(directives.values())
    assert "'unsafe-eval'" not in directives["script-src"]
