"""
base.py — Utilitários partilhados por todos os scrapers.

Contém:
- make_session(): cria uma sessão HTTP com User-Agent identificador
- get_soup(): faz o pedido HTTP e devolve um objeto BeautifulSoup

Porquê usar uma Session em vez de requests.get() direto?
Porque a Session reutiliza a ligação TCP (keep-alive), o que é mais
eficiente quando fazemos dezenas de pedidos ao mesmo domínio.

Porquê o delay? Por cortesia com os servidores. Sem delay, um scraper
pode fazer centenas de pedidos por segundo e sobrecarregar o site.
1 segundo entre pedidos é um compromisso razoável.
"""

from __future__ import annotations

import os
import time
import logging
import requests
import truststore
from bs4 import BeautifulSoup

# Usar o armazém de certificados do sistema operativo (como o browser),
# em vez do bundle Mozilla do certifi. Necessário em redes corporativas
# onde um proxy/antivírus inspeciona HTTPS com certificados próprios.
truststore.inject_into_ssl()

logger = logging.getLogger(__name__)

# User-Agent que identifica o bot de forma transparente.
# Um bot bem-comportado deve identificar-se e indicar contacto.
USER_AGENT = (
    "FeirasBot/1.0 (uso pessoal; rastreador de feiras medievais em Portugal)"
)


def _ssl_verify() -> bool | str:
    """Controla verificação SSL. Por defeito: ativa (via truststore)."""
    value = os.environ.get("SCRAPER_SSL_VERIFY", "1").strip().lower()
    if value in ("0", "false", "no", "off"):
        logger.warning(
            "Verificação SSL desativada (SCRAPER_SSL_VERIFY=%s). "
            "Usar apenas em ambiente de teste.",
            value,
        )
        return False
    return True


def make_session() -> requests.Session:
    """Cria e devolve uma sessão HTTP configurada."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.verify = _ssl_verify()
    return session


def get_soup(url: str, session: requests.Session, delay: float = 1.2) -> BeautifulSoup | None:
    """
    Faz um pedido GET a `url` e devolve um BeautifulSoup.

    Args:
        url:     URL a aceder.
        session: Sessão HTTP a reutilizar.
        delay:   Segundos a esperar antes do pedido (cortesia com o servidor).

    Returns:
        BeautifulSoup se o pedido for bem-sucedido, None caso contrário.
    """
    time.sleep(delay)
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()  # lança exceção para códigos 4xx/5xx
        # lxml é mais rápido que html.parser para documentos grandes
        return BeautifulSoup(resp.text, "lxml")
    except requests.RequestException as e:
        logger.error("Falha ao aceder %s: %s", url, e)
        return None
