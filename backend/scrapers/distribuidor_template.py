"""
TEMPLATE DE SCRAPER — DISTRIBUIDOR EXEMPLO
==========================================
Copie este arquivo e renomeie para o seu distribuidor.
Preencha os seletores CSS corretos inspecionando o portal com DevTools (F12).

DICAS:
  - Abra o portal no Chrome, F12 > Inspector
  - Clique no elemento (campo login, botão, preço etc.)
  - Copie o seletor: botão direito > Copy > Copy selector
  - Cole abaixo substituindo os exemplos

VARIÁVEIS DE AMBIENTE NECESSÁRIAS (.env):
  EXEMPLO_USUARIO=seu_login
  EXEMPLO_SENHA=sua_senha
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scrapers.base_scraper import BaseScraper
from models import Cotacao, StatusCotacao
import re


class DistribuidorExemplo(BaseScraper):

    # ── Identidade ──────────────────────────────────────────────────────────
    DISTRIBUIDOR_ID   = "exemplo"           # usado para ler env vars
    DISTRIBUIDOR_NOME = "Distribuidor Exemplo Ltda"
    URL_BASE          = "https://www.exemplo-distribuidor.com.br"
    URL_LOGIN         = "https://www.exemplo-distribuidor.com.br/login"
    URL_BUSCA         = "https://www.exemplo-distribuidor.com.br/busca"

    # ── Seletores CSS — AJUSTE ESTES PARA CADA PORTAL ───────────────────────
    SEL_INPUT_USUARIO   = "#username"           # campo de login
    SEL_INPUT_SENHA     = "#password"           # campo de senha
    SEL_BTN_LOGIN       = "button[type='submit']"
    SEL_CONFIRMA_LOGIN  = ".user-menu"           # elemento que aparece após login ok

    SEL_INPUT_BUSCA     = "input#search-ref"     # campo de busca por referência
    SEL_BTN_BUSCA       = "button#btn-search"
    SEL_RESULTADO_ITEM  = ".product-item"        # container do produto nos resultados

    SEL_PRECO           = ".price-value"         # ex: "R$ 45,90"
    SEL_ESTOQUE         = ".stock-qty"           # ex: "12 peças"
    SEL_PRAZO           = ".delivery-time"       # ex: "3 dias úteis"
    SEL_DESCRICAO       = ".product-name"

    # ────────────────────────────────────────────────────────────────────────

    async def fazer_login(self) -> bool:
        page = self._page

        # 1. Navega para a página de login
        await page.goto(self.URL_LOGIN)
        await self._aguardar_navegacao()

        # 2. Preenche credenciais
        await self._preencher_campo(self.SEL_INPUT_USUARIO, self.usuario)
        await self._preencher_campo(self.SEL_INPUT_SENHA, self.senha)

        # 3. Clica em entrar
        await self._aguardar_e_clicar(self.SEL_BTN_LOGIN)
        await self._aguardar_navegacao()

        # 4. Verifica se login foi bem-sucedido
        try:
            await page.wait_for_selector(self.SEL_CONFIRMA_LOGIN, timeout=8000)
            return True
        except Exception:
            await self._screenshot_debug("falha_login")
            return False

    async def buscar_referencia(self, referencia: str) -> Cotacao:
        page = self._page

        # 1. Navega para busca (alguns portais têm URL direta)
        await page.goto(self.URL_BUSCA)
        await self._aguardar_navegacao()

        # 2. Digita referência e submete
        await self._preencher_campo(self.SEL_INPUT_BUSCA, referencia)
        await self._aguardar_e_clicar(self.SEL_BTN_BUSCA)
        await self._aguardar_navegacao()

        # 3. Verifica se encontrou resultado
        try:
            await page.wait_for_selector(self.SEL_RESULTADO_ITEM, timeout=10000)
        except Exception:
            return Cotacao(
                distribuidor=self.DISTRIBUIDOR_NOME,
                status=StatusCotacao.NAO_ENCONTRADO,
            )

        # 4. Extrai dados do primeiro resultado
        preco = await self._extrair_preco()
        estoque = await self._extrair_estoque()
        prazo = await self._extrair_prazo()
        descricao = await self._extrair_texto(self.SEL_DESCRICAO)

        if preco is None:
            return Cotacao(
                distribuidor=self.DISTRIBUIDOR_NOME,
                status=StatusCotacao.ERRO,
                erro_msg="Preço não encontrado na página",
            )

        status = StatusCotacao.SUCESSO if (estoque or 0) > 0 else StatusCotacao.SEM_ESTOQUE

        return Cotacao(
            distribuidor=self.DISTRIBUIDOR_NOME,
            preco=preco,
            estoque=estoque,
            prazo_entrega=prazo,
            codigo_produto=descricao,
            status=status,
        )

    # ── Helpers de extração ──────────────────────────────────────────────────

    async def _extrair_preco(self) -> float | None:
        """Extrai preço e converte para float. Adapte o seletor."""
        try:
            texto = await self._extrair_texto(self.SEL_PRECO)
            # Remove R$, pontos de milhar, troca vírgula por ponto
            limpo = re.sub(r"[^\d,]", "", texto).replace(",", ".")
            return float(limpo)
        except Exception:
            return None

    async def _extrair_estoque(self) -> int | None:
        """Extrai quantidade em estoque."""
        try:
            texto = await self._extrair_texto(self.SEL_ESTOQUE)
            numeros = re.findall(r"\d+", texto)
            return int(numeros[0]) if numeros else None
        except Exception:
            return None

    async def _extrair_prazo(self) -> str | None:
        """Extrai prazo de entrega como texto."""
        try:
            return await self._extrair_texto(self.SEL_PRAZO)
        except Exception:
            return None

    async def _extrair_texto(self, seletor: str) -> str:
        elem = await self._page.query_selector(seletor)
        if not elem:
            return ""
        return (await elem.inner_text()).strip()
