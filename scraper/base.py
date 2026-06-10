"""
base.py — Utilitários partilhados por todos os scrapers.

Contém:
- make_session(): cria uma sessão HTTP com headers de browser realistas
- get_soup(): faz o pedido HTTP com retry automático e devolve um BeautifulSoup

Porquê usar uma Session em vez de requests.get() direto?
Porque a Session reutiliza a ligação TCP (keep-alive), o que é mais
eficiente quando fazemos dezenas de pedidos ao mesmo domínio.

Porquê o delay? Por cortesia com os servidores. Sem delay, um scraper
pode fazer centenas de pedidos por segundo e sobrecarregar o site.
1 segundo entre pedidos é um compromisso razoável.
"""

from __future__ import annotations

import os
import platform
import time
import logging
import requests
from bs4 import BeautifulSoup

# truststore: usar certificados do sistema em vez do bundle certifi.
# Necessário em redes corporativas Windows com proxy/antivírus que inspeciona HTTPS.
# Em Linux (GitHub Actions) o SSL do sistema funciona corretamente sem isto.
if platform.system() == "Windows":
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# User-Agent de browser real.
# Porquê não usar "FeirasBot/1.0"? Porque alguns sites WordPress usam WAFs
# (Cloudflare, Wordfence, etc.) que bloqueiam pedidos com User-Agents não-browser,
# retornando 403 ou 415. Um UA de Chrome evita esses bloqueios.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Headers completos que um browser real envia.
# A ausência destes headers foi a causa do erro 415 em feirasmedievais.pt
# quando corrido nos GitHub Actions (o servidor via apenas User-Agent, sem Accept).
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}


def _ssl_verify() -> bool | str:
    """Controla verificação SSL. Por defeito: ativa."""
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
    """Cria e devolve uma sessão HTTP com headers de browser."""
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.verify = _ssl_verify()
    return session


def get_soup(
    url: str,
    session: requests.Session,
    delay: float = 1.2,
    max_retries: int = 3,
) -> BeautifulSoup | None:
    """
    Faz um pedido GET a `url` e devolve um BeautifulSoup.

    Inclui retry com backoff exponencial para erros transitórios de rede.
    Porquê retry? Os GitHub Actions runners podem ser bloqueados temporariamente
    por rate limiting ou o servidor pode ter picos de carga. 3 tentativas com
    5/10/20s de espera cobre a maioria dos casos sem demorar demasiado.

    Args:
        url:         URL a aceder.
        session:     Sessão HTTP a reutilizar.
        delay:       Segundos a esperar antes do primeiro pedido.
        max_retries: Número máximo de tentativas (inclui a primeira).

    Returns:
        BeautifulSoup se o pedido for bem-sucedido, None caso contrário.
    """
    time.sleep(delay)

    for attempt in range(max_retries):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            # lxml é mais rápido que html.parser para documentos grandes
            return BeautifulSoup(resp.text, "lxml")

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            # 429/503/500/502/504 são erros transitórios → vale a pena tentar de novo
            # 4xx permanentes (403, 404, 415…) → não adianta retry
            if status in (429, 500, 502, 503, 504):
                wait = 5 * (2 ** attempt)  # 5s → 10s → 20s
                logger.warning(
                    "HTTP %d em %s (tentativa %d/%d). A aguardar %ds…",
                    status, url, attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
            else:
                logger.error("HTTP %d ao aceder %s: %s", status, url, e)
                return None

        except (requests.ConnectionError, requests.Timeout) as e:
            # Erro de rede (sem ligação, timeout, DNS) → retry
            if attempt < max_retries - 1:
                wait = 5 * (2 ** attempt)
                logger.warning(
                    "Erro de rede em %s (tentativa %d/%d). A aguardar %ds…",
                    url, attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
            else:
                logger.error("Falha persistente ao aceder %s: %s", url, e)
                return None

        except requests.RequestException as e:
            logger.error("Erro inesperado ao aceder %s: %s", url, e)
            return None

    return None
