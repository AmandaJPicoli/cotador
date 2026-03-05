"""
Manager de Scrapers — orquestra cotações em paralelo
"""
import asyncio
import time
import logging
from playwright.async_api import async_playwright

from models import Cotacao, ResultadoCotacao, StatusCotacao
from scrapers.base_scraper import BaseScraper
from scrapers.pitstop import PitStopScraper
from scrapers.wsrpt import WsrptScraper

# ── Registre aqui todos os seus scrapers ──────────────────────────────────
SCRAPERS_REGISTRADOS = [
    PitStopScraper,   # Marketplace VTEX — sem login
    WsrptScraper,     # WSRPT Peças — login JWT
    # DistribuidorA,
]

logger = logging.getLogger(__name__)


class CotacaoManager:

    def __init__(self, scrapers=None):
        self.scrapers_classes = scrapers or SCRAPERS_REGISTRADOS

    async def cotar(
        self,
        referencia: str,
        distribuidores: list[str] | None = None,
        ignorar_sellers: list[str] | None = None,
    ) -> ResultadoCotacao:
        inicio = time.monotonic()
        referencia = referencia.strip()

        classes = self.scrapers_classes
        if distribuidores:
            classes = [c for c in classes if c.DISTRIBUIDOR_ID in distribuidores]

        if not classes:
            return ResultadoCotacao(referencia=referencia)

        scrapers_playwright = [c for c in classes if not getattr(c, "is_marketplace", False)]
        scrapers_api        = [c for c in classes if getattr(c, "is_marketplace", False)]

        tarefas_api = [cls().cotar_multiplo(referencia, ignorar_sellers) for cls in scrapers_api]

        cotacoes_limpas: list[Cotacao] = []
        resultados_pw  = []
        resultados_api = []

        if scrapers_playwright:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                instancias_pw = []
                tarefas_pw    = []
                for cls in scrapers_playwright:
                    inst = cls()
                    await inst.inicializar(browser)
                    instancias_pw.append(inst)
                    tarefas_pw.append(inst.cotar(referencia))

                resultados_pw, resultados_api = await asyncio.gather(
                    asyncio.gather(*tarefas_pw, return_exceptions=True),
                    asyncio.gather(*tarefas_api, return_exceptions=True),
                )

                for inst in instancias_pw:
                    await inst.finalizar()
                await browser.close()
        else:
            resultados_api = await asyncio.gather(*tarefas_api, return_exceptions=True)

        # Processa Playwright
        for i, r in enumerate(resultados_pw):
            nome = scrapers_playwright[i].DISTRIBUIDOR_NOME
            if isinstance(r, Exception):
                logger.error(f"[{nome}] Exceção: {r}")
                cotacoes_limpas.append(Cotacao(
                    distribuidor=nome, status=StatusCotacao.ERRO, erro_msg=str(r),
                ))
            else:
                cotacoes_limpas.append(r)

        # Processa API / marketplace
        for i, r in enumerate(resultados_api):
            nome = scrapers_api[i].DISTRIBUIDOR_NOME
            if isinstance(r, Exception):
                logger.error(f"[{nome}] Exceção: {r}")
                cotacoes_limpas.append(Cotacao(
                    distribuidor=nome, status=StatusCotacao.ERRO, erro_msg=str(r),
                ))
            elif isinstance(r, list):
                cotacoes_limpas.extend(r)
            else:
                cotacoes_limpas.append(r)

        cotacoes_ordenadas = self._ordenar_cotacoes(cotacoes_limpas)
        self._marcar_melhor_preco(cotacoes_ordenadas)

        tempo_ms = int((time.monotonic() - inicio) * 1000)

        return ResultadoCotacao(
            referencia=referencia,
            cotacoes=cotacoes_ordenadas,
            total_consultados=len(cotacoes_ordenadas),
            total_com_estoque=sum(
                1 for c in cotacoes_ordenadas
                if c.status == StatusCotacao.SUCESSO and (c.estoque or 0) > 0
            ),
            tempo_ms=tempo_ms,
        )

    def _ordenar_cotacoes(self, cotacoes: list[Cotacao]) -> list[Cotacao]:
        def chave(c: Cotacao):
            if c.preco is not None and (c.estoque or 0) > 0:
                return (0, c.preco)
            elif c.preco is not None:
                return (1, c.preco)
            else:
                return (2, float("inf"))
        return sorted(cotacoes, key=chave)

    def _marcar_melhor_preco(self, cotacoes: list[Cotacao]):
        precos = [c.preco for c in cotacoes if c.preco is not None]
        if not precos:
            return
        menor = min(precos)
        for c in cotacoes:
            c.melhor_preco = c.preco == menor
