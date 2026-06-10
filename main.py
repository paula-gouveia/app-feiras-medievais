"""
main.py — Runner principal do scraper de Feiras Medievais.

Uso:
    python main.py                              # corre todos os scrapers (uso local)
    SCRAPER_SOURCES=feirasmedievais python main.py  # só feirasmedievais.pt (CI/Actions)

Contexto de execução:
    - LOCAL: corre os dois scrapers. O aondevamos.pt funciona a partir da rede
      doméstica/corporativa mas está bloqueado nos IP ranges do GitHub Actions.
    - GITHUB ACTIONS: usa SCRAPER_SOURCES=feirasmedievais para saltar o aondevamos.

Ficheiros produzidos:
    data/feiras.json   → todos os dados recolhidos
    logs/scraper.log   → log detalhado da execução
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from scraper import feirasmedievais, aondevamos
from scraper.base import make_session

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
# Logging a dois destinos: ficheiro (DEBUG, tudo) e terminal (INFO, resumo).
# Porquê dois handlers? Para não encher o terminal com detalhes de parsing,
# mas ter tudo disponível no ficheiro para debug se algo correr mal.

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        # Ficheiro: regista tudo (DEBUG e acima)
        logging.FileHandler(LOG_DIR / "scraper.log", encoding="utf-8"),
        # Terminal: só INFO e acima (menos ruído)
        logging.StreamHandler(sys.stdout),
    ],
)

# Silenciar logs demasiado verbosos de bibliotecas externas
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("geopy").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Diretório de saída
# ---------------------------------------------------------------------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_FILE = DATA_DIR / "feiras.json"


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def main():
    inicio = datetime.now()
    logger.info("=" * 60)
    logger.info("INÍCIO DO SCRAPER — %s", inicio.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    # Sessão HTTP partilhada pelos dois scrapers (reutiliza conexões TCP)
    session = make_session()

    todas_as_feiras = []

    # SCRAPER_SOURCES controla quais scrapers correm nesta execução.
    # Por defeito (vazio) → corre todos. O GitHub Actions define "feirasmedievais"
    # porque o aondevamos.pt bloqueia os IP ranges dos runners do GitHub.
    # Uso: SCRAPER_SOURCES=feirasmedievais,aondevamos python main.py
    sources_raw = os.environ.get("SCRAPER_SOURCES", "").strip()
    sources = {s.strip().lower() for s in sources_raw.split(",") if s.strip()} or {"feirasmedievais", "aondevamos"}

    if sources != {"feirasmedievais", "aondevamos"}:
        logger.info("Fontes ativas: %s", ", ".join(sorted(sources)))

    # --- Scraper 1: feirasmedievais.pt ---
    feiras_fm = []
    if "feirasmedievais" in sources:
        try:
            feiras_fm = feirasmedievais.run(session)
            todas_as_feiras.extend(feiras_fm)
        except Exception as e:
            logger.exception("Erro inesperado no scraper feirasmedievais.pt: %s", e)
            feiras_fm = []
    else:
        logger.info("feirasmedievais.pt: ignorado (não está em SCRAPER_SOURCES).")

    # --- Scraper 2: aondevamos.pt ---
    # Nota: aondevamos.pt está bloqueado nos runners do GitHub Actions (Errno 101).
    # Para adicionar estes dados, corre `python main.py` localmente e depois
    # `python db/import.py` para sincronizar com o Supabase.
    feiras_av = []
    if "aondevamos" in sources:
        try:
            feiras_av = aondevamos.run(session)
            todas_as_feiras.extend(feiras_av)
        except Exception as e:
            logger.exception("Erro inesperado no scraper aondevamos.pt: %s", e)
            feiras_av = []
    else:
        logger.info("aondevamos.pt: ignorado (não está em SCRAPER_SOURCES).")

    # --- Filtrar posts sem data ---
    # O feirasmedievais.pt mistura posts de eventos com posts editoriais
    # (séries históricas, artigos, etc.). Os editoriais não têm data de evento,
    # por isso descartamo-los aqui. Eventos reais com data de parsing falhado
    # também ficam de fora — preferimos dados limpos a dados duvidosos.
    total_raw = len(todas_as_feiras)
    todas_as_feiras = [f for f in todas_as_feiras if f.data_inicio is not None]
    descartados = total_raw - len(todas_as_feiras)
    if descartados:
        logger.info(
            "Filtro de qualidade: %d entradas sem data descartadas "
            "(posts editoriais ou eventos com formato de data não reconhecido).",
            descartados,
        )

    # Recalcular contagens por fonte após filtro
    n_fm = sum(1 for f in todas_as_feiras if f.fonte == "feirasmedievais")
    n_av = sum(1 for f in todas_as_feiras if f.fonte == "aondevamos")

    # --- Serialização para JSON ---
    output = {
        # Metadados da execução (úteis para saber quando os dados foram recolhidos)
        "meta": {
            "recolhido_em": inicio.isoformat(),
            "total": len(todas_as_feiras),
            "descartados_sem_data": descartados,
            "por_fonte": {
                "feirasmedievais": n_fm,
                "aondevamos": n_av,
            },
        },
        "feiras": [f.to_dict() for f in todas_as_feiras],
    }

    # Escrever via ficheiro temporário + rename atómico para evitar null bytes
    # no Windows quando o ficheiro novo é menor do que o anterior.
    tmp = OUTPUT_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    tmp.replace(OUTPUT_FILE)

    # --- Resumo final ---
    duracao = (datetime.now() - inicio).total_seconds()
    logger.info("=" * 60)
    logger.info("CONCLUÍDO em %.1f segundos", duracao)
    logger.info("  feirasmedievais.pt : %d feiras", n_fm)
    logger.info("  aondevamos.pt      : %d feiras", n_av)
    logger.info("  Descartados        : %d (sem data)", descartados)
    logger.info("  TOTAL              : %d feiras", len(todas_as_feiras))
    logger.info("  Guardado em        : %s", OUTPUT_FILE.resolve())
    logger.info("  Log detalhado em   : %s", (LOG_DIR / 'scraper.log').resolve())
    logger.info("=" * 60)

    # Mostrar uma amostra dos dados recolhidos
    if todas_as_feiras:
        logger.info("\nAmostra (primeiras 3 feiras):")
        for f in todas_as_feiras[:3]:
            logger.info(
                "  [%s] %s | %s → %s | %s, %s | coords: %s",
                f.fonte,
                f.nome,
                f.data_inicio,
                f.data_fim,
                f.localidade,
                f.municipio,
                f"({f.lat:.4f}, {f.lng:.4f})" if f.lat else "sem coords",
            )


if __name__ == "__main__":
    main()
