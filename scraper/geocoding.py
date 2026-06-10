"""
geocoding.py — Converter localidades em coordenadas GPS via Nominatim.

Nominatim é o serviço de geocoding da OpenStreetMap. É gratuito e não
requer API key, mas tem um limite de 1 pedido por segundo (que respeitamos)
e exige um User-Agent identificador (que fornecemos).

Por que usar um cache em memória?
Porque o mesmo município pode aparecer em múltiplas feiras. Sem cache,
faríamos o mesmo pedido ao Nominatim repetidamente — lento e desnecessário.
O cache é um dicionário simples que dura enquanto o scraper estiver a correr.

Nota: feirasmedievais.pt embede coordenadas Google Maps nas páginas de
detalhe, por isso o geocoding só é necessário para eventos do aondevamos.pt
ou para eventos sem mapa incorporado.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

# Cache em memória: chave = query string, valor = (lat, lng) ou None
_cache: dict[str, Optional[tuple[float, float]]] = {}

# Instância única do geocoder (singleton simples)
_geocoder: Optional[Nominatim] = None


def _get_geocoder() -> Nominatim:
    global _geocoder
    if _geocoder is None:
        _geocoder = Nominatim(user_agent="feiras-medievais-pt/1.0 (uso pessoal)")
    return _geocoder


def geocode(localidade: Optional[str], municipio: Optional[str] = None) -> Optional[tuple[float, float]]:
    """
    Converte uma localidade/município em coordenadas (lat, lng).

    Estratégia:
    1. Tenta com "localidade, Portugal"
    2. Se falhar, tenta com "município, Portugal"
    3. Se falhar, retorna None

    Args:
        localidade: Nome da localidade (ex: "Belver").
        municipio:  Nome do município como fallback (ex: "Gavião").

    Returns:
        Tuplo (latitude, longitude) ou None se não encontrado.
    """
    # Constrói as queries a tentar, da mais específica para a mais geral
    queries = []
    if localidade:
        queries.append(f"{localidade}, Portugal")
    if municipio and municipio != localidade:
        queries.append(f"{municipio}, Portugal")

    if not queries:
        return None

    geo = _get_geocoder()

    for query in queries:
        # Verifica o cache primeiro
        if query in _cache:
            cached = _cache[query]
            if cached is not None:
                logger.debug("Cache hit: %s", query)
                return cached
            continue  # já foi tentado e falhou

        # Nominatim exige no mínimo 1 segundo entre pedidos
        time.sleep(1.1)
        try:
            location = geo.geocode(query, language="pt")
            if location:
                result = (location.latitude, location.longitude)
                _cache[query] = result
                logger.info("Geocodificado: %s → %.4f, %.4f", query, *result)
                return result
            else:
                logger.warning("Sem resultado para: %s", query)
                _cache[query] = None

        except GeocoderTimedOut:
            logger.error("Timeout ao geocodificar: %s", query)
        except GeocoderServiceError as e:
            logger.error("Erro no serviço de geocoding para %s: %s", query, e)

    return None
