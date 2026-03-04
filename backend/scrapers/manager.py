"""
Manager de Scrapers — orquestra cotações em paralelo
"""
import asyncio
import time
import logging
from playwright.async_api import async_playwright

from models import Cotacao, ResultadoCotacao, StatusCotacao
from scrapers.base_scraper import BaseScraper

# ── Registre aqui todos os seus scrapers ───────────────────────────────────
# from scrapers.distribuidor_a import DistribuidorA
# from scrapers.distribuidor_b import DistribuidorB
# from scrapers.distribuidor_template import DistribuidorExemplo

SCRAPERS_REGISTRADOS: list[type[BaseScraper]] = [
    # DistribuidorA,
    # DistribuidorB,
    # DistribuidorExemplo,
]

logger = logging.getLogger(__name__)


class CotacaoManager:

    def __init__(self, scrapers: list[type[BaseScraper]] | None = None):
        self.scrapers_classes = scrapers or SCRAPERS_REGISTRADOS

    async def cotar(
        self,
        referencia: str,
        distribuidores: list[str] | None = None,
    ) -> ResultadoCotacao:
        """
        Executa cotações em paralelo nos distribuidores selecionados.
        distribuidores=None → consulta todos registrados.
        """
        inicio = time.monotonic()
        referencia = referencia.strip().upper()

        # Filtra scrapers desejados
        classes = self.scrapers_classes
        if distribuidores:
            classes = [c for c in classes if c.DISTRIBUIDOR_ID in distribuidores]

        if not classes:
            return ResultadoCotacao(referencia=referencia)

        # Roda tudo em paralelo com um único browser (economiza memória)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],  # necessário na nuvem
            )

            tarefas = []
            instancias = []

            for cls in classes:
                instancia = cls()
                await instancia.inicializar(browser)
                instancias.append(instancia)
                tarefas.append(instancia.cotar(referencia))

            resultados: list[Cotacao] = await asyncio.gather(*tarefas, return_exceptions=True)

            # Finaliza contextos
            for inst in instancias:
                await inst.finalizar()

            await browser.close()

        # Trata exceções que vazaram do gather
        cotacoes_limpas: list[Cotacao] = []
        for i, r in enumerate(resultados):
            if isinstance(r, Exception):
                nome = classes[i].DISTRIBUIDOR_NOME
                logger.error(f"[{nome}] Exceção não tratada: {r}")
                cotacoes_limpas.append(Cotacao(
                    distribuidor=nome,
                    status=StatusCotacao.ERRO,
                    erro_msg=str(r),
                ))
            else:
                cotacoes_limpas.append(r)

        # Ordena: com preço primeiro (menor → maior), sem preço por último
        cotacoes_ordenadas = self._ordenar_cotacoes(cotacoes_limpas)

        # Marca menor preço
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
            # Prioridade: tem preço + estoque > tem preço sem estoque > erro/não encontrado
            if c.preco is not None and (c.estoque or 0) > 0:
                return (0, c.preco)
            elif c.preco is not None:
                return (1, c.preco)
            else:
                return (2, float("inf"))

        return sorted(cotacoes, key=chave)

    def _marcar_melhor_preco(self, cotacoes: list[Cotacao]):
        """Marca a(s) cotação(ões) com menor preço disponível."""
        precos_validos = [c.preco for c in cotacoes if c.preco is not None]
        if not precos_validos:
            return
        menor = min(precos_validos)
        for c in cotacoes:
            c.melhor_preco = c.preco == menor
