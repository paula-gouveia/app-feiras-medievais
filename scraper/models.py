"""
models.py — Estrutura de dados de uma Feira Medieval.

Usamos dataclass (biblioteca padrão do Python, sem dependências externas)
para definir os campos de cada feira. O ID é gerado automaticamente com
base no nome + data de início + fonte, garantindo que o mesmo evento
scrapeado duas vezes produz sempre o mesmo ID (estável entre execuções).
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import hashlib


@dataclass
class Feira:
    # Dados essenciais
    nome: str
    data_inicio: Optional[str]      # formato ISO: YYYY-MM-DD
    data_fim: Optional[str]         # formato ISO: YYYY-MM-DD

    # Localização textual
    localidade: Optional[str]       # ex: "Belver"
    municipio: Optional[str]        # ex: "Gavião"
    distrito: Optional[str]         # ex: "Portalegre"

    # Coordenadas GPS (para o mapa)
    lat: Optional[float]
    lng: Optional[float]

    # Conteúdo
    descricao: Optional[str]
    imagem_url: Optional[str]

    # Origem dos dados
    fonte_url: str                  # URL da página de detalhe
    fonte: str                      # "feirasmedievais" | "aondevamos"

    # ID gerado automaticamente — não passar ao construtor
    id: str = field(init=False)

    def __post_init__(self):
        """
        Gera um ID estável de 12 chars baseado em nome + data + fonte.
        Porquê MD5 (e não UUID aleatório)? Porque queremos que o mesmo
        evento, scrapeado em execuções diferentes, produza sempre o mesmo
        ID — isso vai facilitar o upsert na base de dados na Fase 2.
        """
        key = f"{self.nome}|{self.data_inicio or ''}|{self.fonte}"
        self.id = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict:
        """Converte a dataclass para dicionário (pronto para serializar em JSON)."""
        return asdict(self)
