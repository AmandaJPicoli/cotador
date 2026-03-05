"""
API de Cotação de Autopeças
"""

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from models import SolicitacaoCotacao, ResultadoCotacao, Distribuidor
from scrapers.manager import CotacaoManager, SCRAPERS_REGISTRADOS
from scrapers.wsrpt import WsrptScraper

app = FastAPI(title="Cotador Autopeças B2B", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager     = CotacaoManager()
_wsrpt_inst = WsrptScraper()

# ── Credenciais em memória (sobrescreve .env em runtime) ──────────────────
_credenciais: dict[str, dict] = {}


class ConfigPayload(BaseModel):
    credenciais: dict[str, dict]  # { "wsrpt": { "usuario": "...", "senha": "..." } }


@app.get("/health")
async def health():
    return {"status": "ok", "distribuidores": len(SCRAPERS_REGISTRADOS)}


@app.get("/distribuidores", response_model=list[Distribuidor])
async def listar_distribuidores():
    return [
        Distribuidor(id=cls.DISTRIBUIDOR_ID, nome=cls.DISTRIBUIDOR_NOME)
        for cls in SCRAPERS_REGISTRADOS
    ]


@app.post("/config")
async def receber_config(payload: ConfigPayload):
    """
    Recebe credenciais do frontend e atualiza os scrapers em memória.
    Não precisa reiniciar o servidor.
    """
    global _credenciais
    _credenciais = payload.credenciais

    # Atualiza WSRPT imediatamente
    wsrpt_cfg = payload.credenciais.get("wsrpt", {})
    if wsrpt_cfg.get("usuario") and wsrpt_cfg.get("senha"):
        _wsrpt_inst.usuario  = wsrpt_cfg["usuario"]
        _wsrpt_inst.senha    = wsrpt_cfg["senha"]
        _wsrpt_inst._sessao  = None  # força re-login com novas credenciais
        os.environ["WSRPT_USUARIO"] = wsrpt_cfg["usuario"]
        os.environ["WSRPT_SENHA"]   = wsrpt_cfg["senha"]

    # Atualiza os demais scrapers registrados
    for cls in SCRAPERS_REGISTRADOS:
        dist_id = cls.DISTRIBUIDOR_ID
        cfg = payload.credenciais.get(dist_id, {})
        if cfg.get("usuario"):
            os.environ[f"{dist_id.upper()}_USUARIO"] = cfg["usuario"]
        if cfg.get("senha"):
            os.environ[f"{dist_id.upper()}_SENHA"] = cfg["senha"]

    return {"ok": True, "atualizados": list(payload.credenciais.keys())}


@app.post("/cotar", response_model=ResultadoCotacao)
async def cotar(solicitacao: SolicitacaoCotacao):
    if not solicitacao.referencia.strip():
        raise HTTPException(status_code=400, detail="Referência não pode ser vazia")

    resultado = await manager.cotar(
        referencia=solicitacao.referencia,
        distribuidores=solicitacao.distribuidores,
        ignorar_sellers=solicitacao.ignorar_sellers,
    )
    return resultado


@app.get("/ofertas")
async def ofertas():
    """Retorna produtos em oferta do WSRPT."""
    cotacoes = await _wsrpt_inst.ofertas()
    return {
        "cotacoes": [c.model_dump() for c in cotacoes],
        "total": len(cotacoes),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
