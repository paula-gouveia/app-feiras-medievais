"""
db/import.py — Importar o JSON do scraper para o Supabase.

Uso:
    python db/import.py                  # importa data/feiras.json
    python db/import.py data/outro.json  # importa ficheiro específico

Requer as variáveis de ambiente:
    SUPABASE_URL          URL do projeto Supabase (ex: https://xxxx.supabase.co)
    SUPABASE_SERVICE_KEY  Chave service_role (permite escrita, bypassa RLS)

Estas variáveis podem estar num ficheiro .env na raiz do projeto
(o script carrega-o automaticamente se existir).

Como funciona o upsert:
    Para cada feira no JSON, faz INSERT ... ON CONFLICT (id) DO UPDATE.
    Isto significa:
    - Se o ID não existe → insere o registo (nova feira)
    - Se o ID já existe → atualiza todos os campos exceto criado_em
    O campo atualizado_em é gerido automaticamente pelo trigger na BD.

    O ID é um hash estável de (nome + data_inicio + fonte), por isso o mesmo
    evento scrapeado em execuções diferentes tem sempre o mesmo ID.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Carregar .env se existir (para desenvolvimento local)
# Em produção (GitHub Actions) as variáveis vêm de os.environ diretamente
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv não está instalado → sem problema, .env é opcional

import truststore

# Redes corporativas: usar certificados do sistema (como o browser)
truststore.inject_into_ssl()

from supabase import create_client

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
DEFAULT_JSON = Path(__file__).parent.parent / "data" / "feiras.json"
BATCH_SIZE = 100  # Supabase aceita até ~500 registos por request; 100 é seguro


def _normalize_supabase_url(url: str) -> str:
    """
    O cliente Python espera a URL base do projeto, sem /rest/v1/.
    Se o .env tiver o endpoint REST copiado do dashboard, normalizamos.
    """
    url = url.rstrip("/")
    suffix = "/rest/v1"
    if url.endswith(suffix):
        url = url[: -len(suffix)]
    return url


def _get_supabase_client():
    """Cria e devolve o cliente Supabase, validando as variáveis de ambiente."""
    url = _normalize_supabase_url(os.environ.get("SUPABASE_URL", "").strip())
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

    if not url or not key:
        logger.error(
            "Variáveis SUPABASE_URL e/ou SUPABASE_SERVICE_KEY não definidas.\n"
            "Cria um ficheiro .env na raiz do projeto com:\n"
            "  SUPABASE_URL=https://<projeto>.supabase.co\n"
            "  SUPABASE_SERVICE_KEY=<service_role_key>"
        )
        sys.exit(1)

    return create_client(url, key)


def _escolher_melhor_duplicado(atual: dict, novo: dict) -> dict:
    """
    Quando o mesmo ID aparece mais do que uma vez, escolhe o registo
    mais fiável (evita URLs de lixo WordPress; senão fica o último).
    """
    url_atual = atual.get("fonte_url") or ""
    url_novo = novo.get("fonte_url") or ""
    if "__trashed" in url_atual and "__trashed" not in url_novo:
        return novo
    if "__trashed" in url_novo and "__trashed" not in url_atual:
        return atual
    return novo


def _deduplicar_registos(registos: list[dict]) -> list[dict]:
    """
    Garante IDs únicos antes do upsert.

    O PostgreSQL rejeita batches com o mesmo id repetido:
    'ON CONFLICT DO UPDATE command cannot affect row a second time'.
    Isto acontece quando o site republica o mesmo evento com URLs diferentes.
    """
    unicos: dict[str, dict] = {}
    for registo in registos:
        rid = registo["id"]
        if rid in unicos:
            unicos[rid] = _escolher_melhor_duplicado(unicos[rid], registo)
        else:
            unicos[rid] = registo
    return list(unicos.values())


def _preparar_registo(feira: dict) -> dict:
    """
    Converte um dicionário do JSON para o formato esperado pelo Supabase.

    Principais conversões:
    - Campos None mantêm-se None (Supabase guarda como NULL)
    - Datas já estão em formato ISO (YYYY-MM-DD) → compatível com tipo DATE
    - Floats de lat/lng → compatível com DOUBLE PRECISION
    - O campo 'id' do Python torna-se o PRIMARY KEY na BD
    """
    return {
        "id":           feira["id"],
        "nome":         feira["nome"],
        "data_inicio":  feira.get("data_inicio"),
        "data_fim":     feira.get("data_fim"),
        "localidade":   feira.get("localidade"),
        "municipio":    feira.get("municipio"),
        "distrito":     feira.get("distrito"),
        "lat":          feira.get("lat"),
        "lng":          feira.get("lng"),
        "descricao":    feira.get("descricao"),
        "imagem_url":   feira.get("imagem_url"),
        "fonte_url":    feira["fonte_url"],
        "fonte":        feira["fonte"],
        # criado_em é gerido pela BD (DEFAULT NOW()) — não enviamos
        # atualizado_em é gerido pelo trigger da BD — não enviamos
    }


def importar(json_path: Path | None = None) -> None:
    """
    Lê o JSON do scraper e faz upsert de todos os registos no Supabase.

    Args:
        json_path: Caminho para o ficheiro JSON. Por defeito: data/feiras.json
    """
    json_path = json_path or DEFAULT_JSON

    if not json_path.exists():
        logger.error("Ficheiro não encontrado: %s", json_path)
        sys.exit(1)

    logger.info("A ler %s …", json_path)
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    feiras_raw = data.get("feiras", [])
    meta = data.get("meta", {})

    logger.info(
        "JSON: %d feiras (recolhidas em %s)",
        len(feiras_raw),
        meta.get("recolhido_em", "?"),
    )

    if not feiras_raw:
        logger.warning("Nenhuma feira no JSON. Nada a importar.")
        return

    # Preparar registos para o Supabase
    registos = [_preparar_registo(f) for f in feiras_raw]
    total_raw = len(registos)
    registos = _deduplicar_registos(registos)
    if len(registos) < total_raw:
        logger.info(
            "Deduplicados: %d -> %d registos (%d republicacoes ignoradas)",
            total_raw,
            len(registos),
            total_raw - len(registos),
        )

    # Conectar ao Supabase
    client = _get_supabase_client()
    logger.info(
        "Ligado ao Supabase: %s",
        _normalize_supabase_url(os.environ.get("SUPABASE_URL", "")),
    )

    # Upsert em batches
    # Porquê batches? Requests muito grandes podem dar timeout ou erro 413.
    # 100 registos por request é seguro e rápido o suficiente.
    inicio = datetime.now()
    total_upserted = 0
    erros = 0

    for i in range(0, len(registos), BATCH_SIZE):
        batch = registos[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(registos) + BATCH_SIZE - 1) // BATCH_SIZE

        try:
            result = (
                client.table("feiras")
                .upsert(batch, on_conflict="id")
                .execute()
            )
            total_upserted += len(batch)
            logger.info(
                "  Batch %d/%d: %d registos OK",
                batch_num, total_batches, len(batch),
            )
        except Exception as e:
            erros += 1
            logger.error("  Batch %d/%d FALHOU: %s", batch_num, total_batches, e)

    duracao = (datetime.now() - inicio).total_seconds()

    logger.info("=" * 50)
    if erros == 0:
        logger.info("Concluído em %.1f segundos.", duracao)
        logger.info("  %d registos importados/atualizados com sucesso.", total_upserted)
    else:
        logger.warning(
            "Concluído com %d erros. %d/%d registos importados.",
            erros, total_upserted, len(registos),
        )
    logger.info("=" * 50)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    importar(path)
