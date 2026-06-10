"""
aondevamos.py — Scraper para aondevamos.pt/feiras-medievais/

Estrutura do site:
- Também é WordPress. A página de listagem mostra os eventos futuros
  com um "Carregar mais" (JavaScript), mas na prática todos os eventos
  são renderizados no HTML inicial (confirmado via fetch estático).
- Página de detalhe: nome, descrição, data no formato "DD/MM/YYYY - DD/MM/YYYY",
  local, e o município + distrito podem ser extraídos do URL e das meta tags.

Estrutura do URL de detalhe:
  https://aondevamos.pt/<municipio>/<categoria>/<slug-do-evento>/
  ex: https://aondevamos.pt/gaviao/feiras-e-mercados/feira-medieval-de-belver/
  → município = "gavião" → normalizar para "Gavião"

Distrito:
  Disponível na meta og:description: "Feiras e Mercados em Distrito de Portalegre."
  ou no title da página: "... em Distrito de Portalegre ..."

Datas na página de detalhe:
  Dentro de um bloco com o label "DATA":
    19/06/2026 - 21/06/2026
  Formato: DD/MM/YYYY
"""

from __future__ import annotations

import re
import logging
from typing import Optional
from urllib.parse import urlparse
import requests

from .base import get_soup, make_session
from .models import Feira
from .geocoding import geocode

logger = logging.getLogger(__name__)

LISTAGEM_URL = "https://aondevamos.pt/feiras-medievais/"


# ---------------------------------------------------------------------------
# Normalização de texto
# ---------------------------------------------------------------------------

def _capitalizar(texto: Optional[str]) -> Optional[str]:
    """Capitaliza cada palavra de uma string (ex: "gavião" → "Gavião")."""
    if not texto:
        return None
    return " ".join(w.capitalize() for w in texto.replace("-", " ").split())


def _municipio_do_url(url: str) -> Optional[str]:
    """
    Extrai o município do path do URL.

    Exemplo:
      /gaviao/feiras-e-mercados/feira-medieval-de-belver/
      → "Gavião"

    O primeiro segmento do path é sempre o município no aondevamos.pt.
    """
    path = urlparse(url).path.strip("/")
    partes = path.split("/")
    if partes:
        return _capitalizar(partes[0])
    return None


def _distrito_do_meta(soup) -> Optional[str]:
    """
    Extrai o distrito da meta og:description ou do title da página.

    Exemplo de og:description:
      "Feiras e Mercados em Distrito de Portalegre. Acontece a 19 de Junho..."
    """
    # Tenta og:description
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        texto = og_desc.get("content", "")
        m = re.search(r'Distrito de ([A-ZÀ-Ú][a-zà-ú\-\s]+)', texto)
        if m:
            return m.group(1).strip()

    # Tenta o title da página
    title_tag = soup.find("title")
    if title_tag:
        m = re.search(r'Distrito de ([A-ZÀ-Ú][a-zà-ú\-\s]+)', title_tag.get_text())
        if m:
            return m.group(1).strip()

    return None


# ---------------------------------------------------------------------------
# Parsing de datas
# ---------------------------------------------------------------------------

def _parse_data(texto: str) -> tuple[Optional[str], Optional[str]]:
    """
    Converte datas do formato "DD/MM/YYYY - DD/MM/YYYY" para ISO.

    Também aceita um único "DD/MM/YYYY" (evento de 1 dia).

    Returns:
        Tuplo (data_inicio, data_fim) em YYYY-MM-DD, ou (None, None).
    """
    texto = texto.strip()

    # Padrão: "DD/MM/YYYY - DD/MM/YYYY"
    m = re.search(r'(\d{2}/\d{2}/\d{4})\s*[-–]\s*(\d{2}/\d{2}/\d{4})', texto)
    if m:
        d1 = _dmyyyy_para_iso(m.group(1))
        d2 = _dmyyyy_para_iso(m.group(2))
        return d1, d2

    # Padrão: apenas "DD/MM/YYYY"
    m = re.search(r'(\d{2}/\d{2}/\d{4})', texto)
    if m:
        d = _dmyyyy_para_iso(m.group(1))
        return d, d

    return None, None


def _dmyyyy_para_iso(data: str) -> Optional[str]:
    """Converte "DD/MM/YYYY" → "YYYY-MM-DD"."""
    partes = data.split("/")
    if len(partes) == 3:
        d, m, a = partes
        return f"{a}-{m}-{d}"
    return None


# ---------------------------------------------------------------------------
# Scraping da página de detalhe
# ---------------------------------------------------------------------------

def _scrape_detalhe(url: str, session: requests.Session) -> dict:
    """
    Visita a página de detalhe de um evento no aondevamos.pt e extrai:
    - data_inicio, data_fim
    - localidade (do campo "LOCAL")
    - municipio (do URL)
    - distrito (das meta tags)
    - descricao (parágrafo principal)
    - imagem_url (og:image)

    Estrutura relevante da página de detalhe:
        <h1>Feira Medieval de Belver</h1>
        <p>Realiza-se anualmente...</p>  ← descrição
        ...
        <p>DATA</p>
        <p>19/06/2026 - 21/06/2026</p>
        <p>LOCAL</p>
        <p>Castelo de Belver, Portugal</p>
    """
    soup = get_soup(url, session)
    if not soup:
        return {}

    result = {}

    # Município do URL (mais fiável do que tentar extrair do HTML)
    result["municipio"] = _municipio_do_url(url)

    # Distrito das meta tags
    result["distrito"] = _distrito_do_meta(soup)

    # Imagem
    og_img = soup.find("meta", property="og:image")
    if og_img:
        result["imagem_url"] = og_img.get("content")

    # --- Conteúdo principal ---
    # O conteúdo relevante está dentro do article ou da div principal do post
    article = soup.find("article") or soup.find("div", class_=re.compile(r"entry-content|post-content"))

    if not article:
        # Fallback: usar o body inteiro
        article = soup.body

    if not article:
        return result

    linhas = [
        l.strip()
        for l in article.get_text(separator="\n").splitlines()
        if l.strip()
    ]

    # Procurar o bloco DATA / LOCAL
    for i, linha in enumerate(linhas):
        if linha.upper() == "DATA" and i + 1 < len(linhas):
            d_inicio, d_fim = _parse_data(linhas[i + 1])
            if d_inicio:
                result["data_inicio"] = d_inicio
                result["data_fim"] = d_fim

        if linha.upper() == "LOCAL" and i + 1 < len(linhas):
            local_txt = linhas[i + 1]
            # Ex: "Castelo de Belver, Portugal" → localidade = "Castelo de Belver"
            local_partes = local_txt.split(",")
            result["localidade"] = local_partes[0].strip()

    # Descrição: primeiro parágrafo não-vazio antes do bloco DATA
    desc_linhas = []
    for linha in linhas:
        if linha.upper() in ("DATA", "LOCAL", "GUARDAR EVENTO", "PARTILHAR"):
            break
        # Ignorar o nome do evento e linhas de navegação curtas
        if len(linha) > 40:
            desc_linhas.append(linha)
    if desc_linhas:
        result["descricao"] = " ".join(desc_linhas[:3])  # máx. 3 parágrafos

    return result


# ---------------------------------------------------------------------------
# Recolha de links da página de listagem
# ---------------------------------------------------------------------------

def _recolher_links(session: requests.Session) -> list[dict]:
    """
    Extrai todos os links de eventos da página de listagem.

    A página de listagem tem artigos com estrutura:
        <article>
            <h2><a href="...">Nome do Evento</a></h2>
            ...
        </article>

    Nota: O botão "Carregar mais" pode usar JavaScript para eventos muito
    antigos, mas na prática todos os eventos futuros são renderizados
    estaticamente no HTML inicial.
    """
    logger.info("A ler listagem: %s", LISTAGEM_URL)
    soup = get_soup(LISTAGEM_URL, session, delay=1.5)
    if not soup:
        logger.error("Não foi possível aceder à listagem do aondevamos.pt")
        return []

    links = []
    artigos = soup.find_all("article")

    for artigo in artigos:
        h2 = artigo.find("h2")
        if not h2:
            continue
        a = h2.find("a", href=True)
        if not a:
            continue

        href = a["href"].strip()
        nome = a.get_text(strip=True)

        # Limpar sufixos de "Entrada Livre" que aparecem no nome em alguns casos
        nome = re.sub(r'\s*Entrada Livre\s*$', '', nome, flags=re.IGNORECASE).strip()

        if href and nome:
            links.append({"nome": nome, "url": href})

    logger.info("  → %d eventos encontrados na listagem", len(links))
    return links


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def run(session: requests.Session | None = None) -> list[Feira]:
    """
    Executa o scraper completo de aondevamos.pt.

    Fluxo:
    1. Recolher links de eventos da página de listagem.
    2. Para cada evento, visitar a página de detalhe.
    3. Geocodificar a localidade via Nominatim (sem coordenadas nativas).
    4. Construir objetos Feira e devolver a lista.

    Args:
        session: Sessão HTTP a reutilizar (criada automaticamente se None).

    Returns:
        Lista de objetos Feira.
    """
    if session is None:
        session = make_session()

    logger.info("=== SCRAPER: aondevamos.pt ===")

    links = _recolher_links(session)
    logger.info("Total de eventos a processar: %d", len(links))

    feiras: list[Feira] = []

    for item in links:
        logger.info("A processar: %s", item["nome"])
        detalhe = _scrape_detalhe(item["url"], session)

        localidade = detalhe.get("localidade")
        municipio = detalhe.get("municipio")

        # aondevamos.pt não tem Google Maps embed → usamos geocoding
        coords = geocode(localidade, municipio)
        lat, lng = coords if coords else (None, None)
        if not coords:
            logger.warning("Sem coordenadas para: %s (%s, %s)",
                           item["nome"], localidade, municipio)

        feira = Feira(
            nome=item["nome"],
            data_inicio=detalhe.get("data_inicio"),
            data_fim=detalhe.get("data_fim"),
            localidade=localidade,
            municipio=municipio,
            distrito=detalhe.get("distrito"),
            lat=lat,
            lng=lng,
            descricao=detalhe.get("descricao"),
            imagem_url=detalhe.get("imagem_url"),
            fonte_url=item["url"],
            fonte="aondevamos",
        )
        feiras.append(feira)

    logger.info("aondevamos.pt: %d feiras recolhidas.", len(feiras))
    return feiras
