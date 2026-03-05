"""
API de Cotação de Autopeças
Endpoints:
  POST /cotar          — dispara cotação em todos os distribuidores
  GET  /ofertas        — retorna produtos em oferta do WSRPT
  GET  /distribuidores — lista distribuidores registrados
  GET  /health         — healthcheck
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from models import SolicitacaoCotacao, ResultadoCotacao, Distribuidor
from scrapers.manager import CotacaoManager, SCRAPERS_REGISTRADOS
from scrapers.wsrpt import WsrptScraper

app = FastAPI(
    title="Cotador Autopeças B2B",
    description="API para cotação paralela em distribuidores B2B",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager      = CotacaoManager()
_wsrpt_inst  = WsrptScraper()   # instância única — reutiliza sessão entre requests


@app.get("/health")
async def health():
    return {"status": "ok", "distribuidores": len(SCRAPERS_REGISTRADOS)}


@app.get("/distribuidores", response_model=list[Distribuidor])
async def listar_distribuidores():
    return [
        Distribuidor(id=cls.DISTRIBUIDOR_ID, nome=cls.DISTRIBUIDOR_NOME)
        for cls in SCRAPERS_REGISTRADOS
    ]


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
    """Retorna todos os produtos em oferta do WSRPT."""
    cotacoes = await _wsrpt_inst.ofertas()
    return {
        "cotacoes": [c.model_dump() for c in cotacoes],
        "total": len(cotacoes),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
