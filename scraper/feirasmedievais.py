"""
feirasmedievais.py — Scraper para feirasmedievais.pt

Estrutura do site:
- É um WordPress com posts de blog para cada evento.
- Página principal (e /page/2/, /page/3/, ...): lista de artigos em cards.
- Página de detalhe de cada artigo: nome, data, localidade, município,
  website, organização, e um Google Maps embed com coordenadas GPS.

Estratégia de scraping:
1. Percorrer as páginas de listagem e recolher os links dos artigos.
2. Para cada artigo, visitar a página de detalhe e extrair os dados.
3. As coordenadas GPS são extraídas diretamente do URL do Google Maps embed
   (mais fiável e preciso do que geocoding — sem chamadas externas).
4. Se não houver mapa embed, cai para geocoding via Nominatim.

Formato das datas no site:
- "21 de Junho, 2026"          → evento de 1 dia
- "5 a 7 de Junho, 2026"       → evento multi-dia mesmo mês
- "20 e 21 de Junho, 2026"     → evento 2 dias mesmo mês
- "28 de Junho a 1 de Julho, 2026" → evento que cruza meses
"""

from __future__ import annotations

import re
import logging
from typing import Optional
import requests

from .base import get_soup, make_session
from .models import Feira
from .geocoding import geocode

logger = logging.getLogger(__name__)

BASE_URL = "https://feirasmedievais.pt"

# Mapeamento de nomes de meses em português para números
MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


# ---------------------------------------------------------------------------
# Parsing de datas
# ---------------------------------------------------------------------------

def _parse_data(texto: str) -> tuple[Optional[str], Optional[str]]:
    """
    Tenta extrair datas de início e fim de uma string em português.

    Exemplos de input:
        "5 a 7 de Junho, 2026"
        "20 e 21 de Junho, 2026"
        "21 de Junho, 2026"
        "28 de Junho a 1 de Julho, 2026"

    Retorna tuplo (data_inicio, data_fim) em formato YYYY-MM-DD,
    ou (None, None) se não reconhecer o padrão.
    """
    t = texto.strip().lower()

    # Padrão: "D a D de Mês, YYYY" ou "D e D de Mês, YYYY" (mesmo mês)
    m = re.search(r'(\d{1,2})\s+[ae]\s+(\d{1,2})\s+de\s+(\w+)[,\s]+(\d{4})', t)
    if m:
        d1, d2, mes, ano = m.groups()
        month = MESES.get(mes)
        if month:
            return (
                f"{ano}-{month:02d}-{int(d1):02d}",
                f"{ano}-{month:02d}-{int(d2):02d}",
            )

    # Padrão: "D de Mês a D de Mês, YYYY" (meses diferentes)
    m = re.search(
        r'(\d{1,2})\s+de\s+(\w+)\s+a\s+(\d{1,2})\s+de\s+(\w+)[,\s]+(\d{4})', t
    )
    if m:
        d1, mes1, d2, mes2, ano = m.groups()
        month1, month2 = MESES.get(mes1), MESES.get(mes2)
        if month1 and month2:
            return (
                f"{ano}-{month1:02d}-{int(d1):02d}",
                f"{ano}-{month2:02d}-{int(d2):02d}",
            )

    # Padrão: "D de Mês, YYYY" (evento de 1 dia)
    m = re.search(r'(\d{1,2})\s+de\s+(\w+)[,\s]+(\d{4})', t)
    if m:
        d, mes, ano = m.groups()
        month = MESES.get(mes)
        if month:
            date_str = f"{ano}-{month:02d}-{int(d):02d}"
            return date_str, date_str

    return None, None


# ---------------------------------------------------------------------------
# Validação geográfica
# ---------------------------------------------------------------------------

def _coords_validas(lat: float, lng: float) -> bool:
    """
    Verifica se as coordenadas estão numa região geograficamente plausível
    para um evento organizado ou listado por um site português.

    Regiões aceites (inclui Espanha, pois o site lista eventos ibéricos):
    - Península Ibérica (Portugal + Espanha + Andorra)
    - Madeira
    - Açores

    Porquê este check?
    O Google Maps embed no feirasmedievais.pt usa o parâmetro `!2d`/`!3d`
    para o CENTRO DO VIEWPORT do mapa, não para o pin do evento. Quando o
    editor deixa o zoom muito afastado (a mostrar Portugal inteiro), o centro
    fica à volta de lat=39.3, lng=-9.2 (centro de Portugal) e os valores
    extraídos ficam errados. Com um viewport estreito (zoom próximo do local),
    os valores são corretos. Este validador deteta os casos errados: coordenadas
    com longitude mais a oeste do que qualquer terra portuguesa (< -9.6°)
    acabam no Atlântico e são claramente inválidas.
    """
    # Península Ibérica
    if 35.9 <= lat <= 43.8 and -9.6 <= lng <= 4.4:
        return True
    # Madeira
    if 32.5 <= lat <= 33.2 and -17.4 <= lng <= -16.3:
        return True
    # Açores (da ilha das Flores às ilhas de São Miguel/Santa Maria)
    if 36.9 <= lat <= 39.8 and -31.3 <= lng <= -25.0:
        return True
    return False


# ---------------------------------------------------------------------------
# Extração de coordenadas do Google Maps embed
# ---------------------------------------------------------------------------

def _extrair_coords_maps(soup) -> tuple[Optional[float], Optional[float]]:
    """
    Extrai latitude e longitude do URL de um Google Maps iframe embutido.

    O URL do embed tem o formato:
      ...!2d<longitude>!3d<latitude>...
    onde !2d = longitude e !3d = latitude.

    Exemplo: !2d-9.1632611971663!3d39.297950890042664
      → lat=39.2980, lng=-9.1633

    Devolve (None, None) se não encontrar iframe, se o URL não tiver o padrão
    esperado, ou se as coordenadas extraídas falharem a validação geográfica
    (o que indica que o mapa estava com zoom muito afastado e os valores
    correspondem ao centro do viewport, não ao local do evento).
    """
    iframe = soup.find("iframe", src=re.compile(r"maps\.google\.com|google\.com/maps"))
    if not iframe:
        return None, None

    src = iframe.get("src", "")
    m = re.search(r'!2d([-\d.]+)!3d([-\d.]+)', src)
    if m:
        lng = float(m.group(1))
        lat = float(m.group(2))
        if _coords_validas(lat, lng):
            return lat, lng
        else:
            logger.debug(
                "Coordenadas extraídas do Maps fora dos limites válidos "
                "(%.4f, %.4f) — provável viewport afastado, a usar geocoding.",
                lat, lng,
            )

    return None, None


# ---------------------------------------------------------------------------
# Scraping da página de detalhe
# ---------------------------------------------------------------------------

def _scrape_detalhe(url: str, session: requests.Session) -> dict:
    """
    Visita a página de detalhe de um evento e extrai os campos disponíveis.

    Estrutura típica do conteúdo:
        <nome do evento (repetido)>
        <subtítulo opcional>
        <data>         ← ex: "21 de Junho, 2026"
        <dia da semana>
        <localidade,município>  ← ex: "Belver,Gavião"
        Web Site / Organização / etc.
        <Google Maps iframe>

    Retorna um dicionário com os campos encontrados.
    """
    soup = get_soup(url, session)
    if not soup:
        return {}

    result = {}

    # --- Imagem (og:image é a mais fiável) ---
    og_img = soup.find("meta", property="og:image")
    if og_img:
        result["imagem_url"] = og_img.get("content")

    # --- Coordenadas do Google Maps embed ---
    lat, lng = _extrair_coords_maps(soup)
    result["lat"] = lat
    result["lng"] = lng

    # --- Conteúdo do post ---
    # O Divi (tema WordPress usado) coloca o conteúdo em .et_pb_post_content
    content_div = soup.find("div", class_=re.compile(r"et_pb_post_content|entry-content"))
    if not content_div:
        return result

    # Extrair linhas de texto não vazias do conteúdo
    linhas = [
        l.strip()
        for l in content_div.get_text(separator="\n").splitlines()
        if l.strip()
    ]

    # Percorrer as linhas à procura de data e localização
    for i, linha in enumerate(linhas):
        d_inicio, d_fim = _parse_data(linha)
        if d_inicio:
            result["data_inicio"] = d_inicio
            result["data_fim"] = d_fim

            # A linha de localidade/município costuma vir 2 linhas depois
            # (com o dia da semana no meio, ex: "Domingo")
            for j in range(i + 1, min(i + 4, len(linhas))):
                loc = linhas[j]
                # Ignorar linhas que claramente são metadados
                if any(
                    kw in loc.lower()
                    for kw in ["web", "www", "http", "organiz", "apoios",
                               "direção", "clique", "segunda", "terça",
                               "quarta", "quinta", "sexta", "sábado", "domingo"]
                ):
                    continue

                # Separar localidade e município (separados por vírgula)
                partes = [p.strip() for p in loc.split(",") if p.strip()]
                if partes:
                    result["localidade"] = partes[0]
                    result["municipio"] = partes[1] if len(partes) > 1 else None
                break

            break  # já encontrámos a data, saímos do loop

    return result


# ---------------------------------------------------------------------------
# Recolha de links de artigos (paginação)
# ---------------------------------------------------------------------------

def _extrair_link_artigo(artigo) -> dict | None:
    """
    Extrai nome e URL de um <article> de listagem.

    O tema Divi mudou de h2 para h1 nos títulos; o link pode estar no
    heading ou num <a> irmão com o mesmo texto.
    """
    classes = artigo.get("class", [])
    if "type-post" not in classes:
        return None

    heading = artigo.find(["h1", "h2"])
    if not heading:
        return None

    nome = heading.get_text(strip=True)
    if not nome:
        return None

    a = heading.find("a", href=True)
    if not a:
        for link in artigo.find_all("a", href=True):
            if link.get_text(strip=True) == nome:
                a = link
                break
    if not a:
        return None

    href = a["href"].strip()
    if not href or href.startswith("#"):
        return None

    return {"nome": nome, "url": href}


def _tem_pagina_seguinte(soup, pagina_atual: int) -> bool:
    """Deteta link para a página seguinte (Divi usa 'Older Entries')."""
    proxima = f"/page/{pagina_atual + 1}"
    for a in soup.find_all("a", href=True):
        if proxima in a["href"]:
            return True
    return bool(soup.find("a", class_=re.compile(r"\bnext\b")))


def _recolher_links(session: requests.Session) -> list[dict]:
    """
    Percorre as páginas de listagem e recolhe links + nomes dos artigos.

    A paginação funciona assim:
    - Página 1: https://feirasmedievais.pt/
    - Página 2: https://feirasmedievais.pt/page/2/
    - ...
    Paramos quando não há link para a página seguinte ou a página não tem posts.
    """
    links: list[dict] = []
    urls_vistas: set[str] = set()
    pagina = 1

    while True:
        url = BASE_URL if pagina == 1 else f"{BASE_URL}/page/{pagina}/"
        logger.info("Listagem página %d: %s", pagina, url)

        soup = get_soup(url, session, delay=1.5)
        if not soup:
            logger.warning("Não foi possível aceder à página %d. A parar.", pagina)
            break

        artigos = soup.find_all("article", class_=re.compile(r"\btype-post\b"))
        if not artigos:
            logger.info("Nenhum artigo encontrado na página %d. Fim da paginação.", pagina)
            break

        novos = 0
        for artigo in artigos:
            item = _extrair_link_artigo(artigo)
            if not item or item["url"] in urls_vistas:
                continue
            urls_vistas.add(item["url"])
            links.append(item)
            novos += 1

        logger.info("  → %d artigos encontrados nesta página", novos)

        if not _tem_pagina_seguinte(soup, pagina):
            logger.info("Sem página seguinte. Fim da paginação.")
            break

        pagina += 1

    return links


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def run(session: requests.Session | None = None) -> list[Feira]:
    """
    Executa o scraper completo de feirasmedievais.pt.

    Fluxo:
    1. Recolher todos os links de artigos (todas as páginas de listagem).
    2. Para cada artigo, visitar a página de detalhe.
    3. Construir objetos Feira e devolver a lista.

    Args:
        session: Sessão HTTP a reutilizar (criada automaticamente se None).

    Returns:
        Lista de objetos Feira.
    """
    if session is None:
        session = make_session()

    logger.info("=== SCRAPER: feirasmedievais.pt ===")

    links = _recolher_links(session)
    logger.info("Total de artigos a processar: %d", len(links))

    feiras: list[Feira] = []

    for item in links:
        logger.info("A processar: %s", item["nome"])
        detalhe = _scrape_detalhe(item["url"], session)

        lat = detalhe.get("lat")
        lng = detalhe.get("lng")
        localidade = detalhe.get("localidade")
        municipio = detalhe.get("municipio")

        # Se não há coordenadas no mapa embed, usamos geocoding
        if lat is None:
            coords = geocode(localidade, municipio)
            if coords:
                lat, lng = coords
            else:
                logger.warning("Sem coordenadas para: %s (%s, %s)",
                               item["nome"], localidade, municipio)

        feira = Feira(
            nome=item["nome"],
            data_inicio=detalhe.get("data_inicio"),
            data_fim=detalhe.get("data_fim"),
            localidade=localidade,
            municipio=municipio,
            distrito=None,   # feirasmedievais.pt não tem distrito explícito
            lat=lat,
            lng=lng,
            descricao=None,  # os posts raramente têm descrição estruturada
            imagem_url=detalhe.get("imagem_url"),
            fonte_url=item["url"],
            fonte="feirasmedievais",
        )
        feiras.append(feira)

    logger.info("feirasmedievais.pt: %d feiras recolhidas.", len(feiras))
    return feiras
