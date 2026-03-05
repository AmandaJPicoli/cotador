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

@app.get("/debug-login")
async def debug_login():
    """
    Dispara o login do WSRPT e retorna log detalhado + lista de screenshots gerados.
    Acesse via: GET http://localhost:8000/debug-login
    """
    import logging
    import io
    import os

    # Captura os logs em memória
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.DEBUG)
    wsrpt_logger = logging.getLogger("scrapers.wsrpt")
    wsrpt_logger.setLevel(logging.DEBUG)
    wsrpt_logger.addHandler(handler)

    from scrapers.wsrpt import autenticar, URL_BASE
    usuario = os.getenv("WSRPT_USUARIO", "")
    senha   = os.getenv("WSRPT_SENHA",   "")

    if not usuario:
        return {"erro": "WSRPT_USUARIO não configurado. Salve as credenciais na aba Configurações primeiro."}

    sessao = await autenticar(usuario, senha)

    wsrpt_logger.removeHandler(handler)
    logs = log_stream.getvalue().splitlines()

    screenshots = [
        f for f in [
            "/tmp/wsrpt_1_login_carregado.png",
            "/tmp/wsrpt_2_campos_preenchidos.png",
            "/tmp/wsrpt_3_pos_login.png",
            "/tmp/wsrpt_4_busca_dummy.png",
            "/tmp/wsrpt_login_erro.png",
        ]
        if os.path.exists(f)
    ]

    return {
        "sessao_ok":    sessao.ok(),
        "pedido":       sessao.pedido or "(não capturado)",
        "firma":        sessao.firma,
        "local":        sessao.local,
        "cookies":      list(sessao.cookies.keys()),
        "logs":         logs,
        "screenshots":  screenshots,
        "instrucao":    "Se sessao_ok=false, verifique os logs acima e os screenshots em /tmp/wsrpt_*.png no servidor",
    }


