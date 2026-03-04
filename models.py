from pydantic import BaseModel
from typing import Optional
from enum import Enum


class StatusCotacao(str, Enum):
    SUCESSO = "sucesso"
    ERRO = "erro"
    SEM_ESTOQUE = "sem_estoque"
    NAO_ENCONTRADO = "nao_encontrado"


class Cotacao(BaseModel):
    distribuidor: str
    preco: Optional[float] = None
    estoque: Optional[int] = None
    prazo_entrega: Optional[str] = None
    unidade: Optional[str] = None
    codigo_produto: Optional[str] = None
    status: StatusCotacao = StatusCotacao.SUCESSO
    erro_msg: Optional[str] = None
    melhor_preco: bool = False  # marcado pelo manager


class ResultadoCotacao(BaseModel):
    referencia: str
    descricao: Optional[str] = None
    cotacoes: list[Cotacao] = []
    total_consultados: int = 0
    total_com_estoque: int = 0
    tempo_ms: Optional[int] = None


class SolicitacaoCotacao(BaseModel):
    referencia: str
    distribuidores: Optional[list[str]] = None  # None = todos


class Distribuidor(BaseModel):
    id: str
    nome: str
    ativo: bool = True
