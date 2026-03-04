from abc import ABC, abstractmethod
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from models import Cotacao, StatusCotacao
import logging
import os

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """
    Classe base para todos os scrapers de distribuidores.
    Cada distribuidor deve herdar desta classe e implementar os métodos abstratos.
    """

    # Defina estes atributos na subclasse
    DISTRIBUIDOR_ID: str = ""
    DISTRIBUIDOR_NOME: str = ""
    URL_BASE: str = ""
    URL_LOGIN: str = ""
    URL_BUSCA: str = ""

    def __init__(self):
        self.usuario = os.getenv(f"{self.DISTRIBUIDOR_ID.upper()}_USUARIO", "")
        self.senha = os.getenv(f"{self.DISTRIBUIDOR_ID.upper()}_SENHA", "")
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def inicializar(self, browser: Browser):
        """Cria contexto isolado por distribuidor (cookies separados)."""
        self._context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
        )
        self._page = await self._context.new_page()

    async def finalizar(self):
        """Fecha contexto do distribuidor."""
        if self._context:
            await self._context.close()

    @abstractmethod
    async def fazer_login(self) -> bool:
        """
        Faz login no portal. Retorna True se sucesso.
        Implemente: navegação para URL_LOGIN, preenchimento de campos, submit.
        """
        ...

    @abstractmethod
    async def buscar_referencia(self, referencia: str) -> Cotacao:
        """
        Busca a referência no portal e extrai preço, estoque e prazo.
        Retorna um objeto Cotacao preenchido.
        """
        ...

    async def cotar(self, referencia: str) -> Cotacao:
        """
        Método principal chamado pelo manager.
        Orquestra login + busca com tratamento de erros.
        """
        try:
            logado = await self.fazer_login()
            if not logado:
                return Cotacao(
                    distribuidor=self.DISTRIBUIDOR_NOME,
                    status=StatusCotacao.ERRO,
                    erro_msg="Falha no login",
                )
            return await self.buscar_referencia(referencia)

        except Exception as e:
            logger.error(f"[{self.DISTRIBUIDOR_NOME}] Erro: {e}")
            return Cotacao(
                distribuidor=self.DISTRIBUIDOR_NOME,
                status=StatusCotacao.ERRO,
                erro_msg=str(e),
            )

    # ─── Helpers reutilizáveis ───────────────────────────────────────────────

    async def _aguardar_e_clicar(self, seletor: str, timeout: int = 10000):
        await self._page.wait_for_selector(seletor, timeout=timeout)
        await self._page.click(seletor)

    async def _preencher_campo(self, seletor: str, valor: str):
        await self._page.wait_for_selector(seletor)
        await self._page.fill(seletor, valor)

    async def _aguardar_navegacao(self, timeout: int = 15000):
        await self._page.wait_for_load_state("networkidle", timeout=timeout)

    async def _screenshot_debug(self, nome: str = "debug"):
        """Útil durante desenvolvimento para ver o estado da página."""
        await self._page.screenshot(path=f"/tmp/{nome}_{self.DISTRIBUIDOR_ID}.png")
