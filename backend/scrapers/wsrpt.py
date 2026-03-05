"""
WSRPT Peças — Scraper via API REST
====================================
Portal: http://wsrpt.pecas.com.br

Fluxo:
  1. Playwright faz login e captura:
       - Cookie/token de sessão
       - ID do pedido ativo (parâmetro "pedido")
       - firma e local do usuário
  2. httpx usa esses dados diretamente nas APIs:
       GET /api/v2/ws/ws/produtos?pedido=X&words=termo   → lista de produtos
       GET /api/v2/ws/ws/precos?id=X&firma=F&local=L&produto=ID → preço unitário
  3. Preços são buscados em paralelo para os top N produtos com estoque

Credenciais via .env:
  WSRPT_USUARIO=email@exemplo.com
  WSRPT_SENHA=senha123
"""

import asyncio
import time
import re
import json
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import httpx
from playwright.async_api import async_playwright

from models import Cotacao, StatusCotacao

logger = logging.getLogger(__name__)

DISTRIBUIDOR_ID   = "wsrpt"
DISTRIBUIDOR_NOME = "WSRPT Peças"
URL_BASE          = "http://wsrpt.pecas.com.br"
URL_LOGIN         = "http://wsrpt.pecas.com.br/account/login?ReturnUrl=%2F"

# Quantos produtos buscar preço (os com mais estoque primeiro)
MAX_PRODUTOS_PRECO = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer":         URL_BASE + "/",
    "X-Requested-With": "XMLHttpRequest",
}


# ══════════════════════════════════════════════════════════
#  SESSÃO — captura pedido, firma, local via Playwright
# ══════════════════════════════════════════════════════════

class SessaoWsrpt:
    """Guarda os dados de sessão após o login."""
    pedido: str = ""
    firma:  str = "82"
    local:  str = "65"
    cookies: dict = {}

    def ok(self) -> bool:
        return bool(self.pedido and self.cookies)


async def autenticar(usuario: str, senha: str) -> SessaoWsrpt:
    """
    Faz login via Playwright, intercepta as chamadas de API para capturar
    o ID do pedido, firma e local. Retorna SessaoWsrpt preenchida.
    """
    sessao = SessaoWsrpt()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()

        # ── Intercepta requests para capturar o pedido ───────────────
        async def on_request(request):
            url = request.url
            # O pedido aparece nos parâmetros das chamadas de API após login
            if "/api/v2/ws/ws/" in url:
                params = _extrair_params_url(url)
                pedido = params.get("pedido") or params.get("id", "")
                firma  = params.get("firma", sessao.firma)
                local  = params.get("local", sessao.local)
                if pedido and not sessao.pedido:
                    sessao.pedido = pedido
                    sessao.firma  = firma
                    sessao.local  = local
                    logger.info(f"[WSRPT] Sessão capturada: pedido={pedido} firma={firma} local={local}")

        page.on("request", on_request)

        try:
            # Navega para login
            await page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(1)

            # Preenche e-mail
            for sel in ["input[type='email']", "input[name='Email']",
                        "#Email", "#email", "input[placeholder*='mail' i]"]:
                try:
                    await page.wait_for_selector(sel, timeout=3000)
                    await page.fill(sel, usuario)
                    logger.debug(f"[WSRPT] E-mail preenchido: {sel}")
                    break
                except Exception:
                    pass

            # Preenche senha
            for sel in ["input[type='password']", "input[name='Password']",
                        "#Password", "#password"]:
                try:
                    await page.wait_for_selector(sel, timeout=3000)
                    await page.fill(sel, senha)
                    logger.debug(f"[WSRPT] Senha preenchida: {sel}")
                    break
                except Exception:
                    pass

            # Submit
            for sel in ["button[type='submit']", "input[type='submit']",
                        "button:has-text('Entrar')", "button:has-text('Login')",
                        "button:has-text('Acessar')"]:
                try:
                    await page.click(sel, timeout=3000)
                    logger.debug(f"[WSRPT] Submit clicado: {sel}")
                    break
                except Exception:
                    pass

            await page.wait_for_load_state("networkidle", timeout=20000)
            await asyncio.sleep(3)

            # Captura cookies da sessão
            cookies_list = await ctx.cookies()
            sessao.cookies = {c["name"]: c["value"] for c in cookies_list}

            # Se ainda não capturou o pedido, faz uma busca dummy para forçar
            if not sessao.pedido:
                logger.debug("[WSRPT] Pedido não capturado ainda — tentando busca dummy")
                for sel in ["input[type='search']", "input[placeholder*='busca' i]",
                             "input[placeholder*='referência' i]", "input[placeholder*='peça' i]",
                             "input[name='search']", "#search"]:
                    try:
                        await page.wait_for_selector(sel, timeout=3000)
                        await page.fill(sel, "filtro")
                        await page.press(sel, "Enter")
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        await asyncio.sleep(2)
                        break
                    except Exception:
                        pass

            # Última tentativa: busca o pedido no HTML da página
            if not sessao.pedido:
                html = await page.content()
                match = re.search(r'pedido["\s:=]+(["\']?)(\d{10,})\1', html)
                if match:
                    sessao.pedido = match.group(2)
                    logger.info(f"[WSRPT] Pedido extraído do HTML: {sessao.pedido}")

        except Exception as e:
            logger.error(f"[WSRPT] Erro no login: {e}")
            await page.screenshot(path="/tmp/wsrpt_login_erro.png")

        await browser.close()

    return sessao


# ══════════════════════════════════════════════════════════
#  API — busca de produtos
# ══════════════════════════════════════════════════════════

async def buscar_produtos(sessao: SessaoWsrpt, referencia: str) -> list[dict]:
    """
    GET /api/v2/ws/ws/produtos?pedido=X&words=termo
    Retorna lista de produtos (sem preço ainda).
    """
    ts = int(time.time() * 1000)
    params = {
        "pedido": sessao.pedido,
        "words":  referencia,
        "aplic":  "",
        "top3":   "",
        "_":      ts,
    }

    async with httpx.AsyncClient(
        headers={**HEADERS, "Cookie": _cookies_str(sessao.cookies)},
        base_url=URL_BASE,
        timeout=20,
    ) as client:
        resp = await client.get("/api/v2/ws/ws/produtos", params=params)
        resp.raise_for_status()
        data = resp.json()

    produtos = data.get("produtos", [])
    logger.info(f"[WSRPT] '{referencia}' → {len(produtos)} produto(s)")
    return produtos


# ══════════════════════════════════════════════════════════
#  API — preço por produto
# ══════════════════════════════════════════════════════════

async def buscar_preco(
    client: httpx.AsyncClient,
    sessao: SessaoWsrpt,
    codigo_interno: int,
) -> float | None:
    """
    GET /api/v2/ws/ws/precos?id=X&firma=F&local=L&produto=ID
    Retorna o preço do produto para este usuário.
    """
    ts = int(time.time() * 1000)
    params = {
        "id":      sessao.pedido,
        "firma":   sessao.firma,
        "local":   sessao.local,
        "produto": codigo_interno,
        "_":       ts,
    }
    try:
        resp = await client.get("/api/v2/ws/ws/precos", params=params)
        resp.raise_for_status()
        data = resp.json()
        preco = data.get("preco")
        return float(preco) if preco else None
    except Exception as e:
        logger.debug(f"[WSRPT] Preço produto {codigo_interno}: {e}")
        return None


# ══════════════════════════════════════════════════════════
#  ORQUESTRA — busca + preços em paralelo
# ══════════════════════════════════════════════════════════

async def buscar_ofertas(sessao: SessaoWsrpt) -> list[Cotacao]:
    """
    Retorna todos os produtos em oferta do distribuidor.
    Usa o termo especial: words=b2b-ofertas=sortidos-1
    """
    try:
        produtos = await buscar_produtos(sessao, "b2b-ofertas=sortidos-1")
    except Exception as e:
        return [Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            status=StatusCotacao.ERRO,
            erro_msg=f"Falha ao buscar ofertas: {e}",
        )]

    if not produtos:
        return []

    # Filtra só os que têm estoque
    def estoque_total(p):
        return (p.get("estoque") or 0) + sum(f.get("saldo", 0) for f in (p.get("estoques") or []))

    com_estoque = [p for p in produtos if estoque_total(p) > 0]
    candidatos  = sorted(com_estoque, key=lambda p: p.get("seq", 9999))[:MAX_PRODUTOS_PRECO * 2]

    async with httpx.AsyncClient(
        headers={**HEADERS, "Cookie": _cookies_str(sessao.cookies)},
        base_url=URL_BASE,
        timeout=20,
    ) as client:
        precos = await asyncio.gather(*[
            buscar_preco(client, sessao, p["codigo_interno"]) for p in candidatos
        ], return_exceptions=True)

    cotacoes = []
    for produto, preco_resultado in zip(candidatos, precos):
        preco = preco_resultado if isinstance(preco_resultado, float) else None
        est   = estoque_total(produto)
        if not preco:
            continue
        cotacoes.append(Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            preco=preco,
            estoque=est,
            codigo_produto=f"{produto.get('codigo', '')} — {produto.get('descricao', '')} [{produto.get('marca', '')}]",
            status=StatusCotacao.SUCESSO if est > 0 else StatusCotacao.SEM_ESTOQUE,
        ))

    cotacoes.sort(key=lambda c: c.preco or 9999)
    logger.info(f"[WSRPT] Ofertas: {len(cotacoes)} produto(s) com preço")
    return cotacoes


async def cotar_wsrpt(sessao: SessaoWsrpt, referencia: str) -> list[Cotacao]:
    """
    Fluxo completo:
      1. Busca produtos pelo termo
      2. Seleciona os que têm estoque (ou filiais) — até MAX_PRODUTOS_PRECO
      3. Busca preços em paralelo
      4. Retorna lista de Cotacao ordenada
    """
    try:
        produtos = await buscar_produtos(sessao, referencia)
    except Exception as e:
        return [Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            status=StatusCotacao.ERRO,
            erro_msg=f"Busca falhou: {e}",
        )]

    if not produtos:
        return [Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            status=StatusCotacao.NAO_ENCONTRADO,
        )]

    # Calcula estoque total (local + filiais)
    def estoque_total(p: dict) -> int:
        local = p.get("estoque") or 0
        filiais = sum(f.get("saldo", 0) for f in (p.get("estoques") or []))
        return local + filiais

    # Ordena: com estoque primeiro, depois por seq
    produtos_ord = sorted(
        produtos,
        key=lambda p: (0 if estoque_total(p) > 0 else 1, p.get("seq", 9999))
    )

    # Pega os top N para buscar preço
    candidatos = produtos_ord[:MAX_PRODUTOS_PRECO]

    # Busca todos os preços em paralelo
    async with httpx.AsyncClient(
        headers={**HEADERS, "Cookie": _cookies_str(sessao.cookies)},
        base_url=URL_BASE,
        timeout=20,
    ) as client:
        tarefas = [
            buscar_preco(client, sessao, p["codigo_interno"])
            for p in candidatos
        ]
        precos = await asyncio.gather(*tarefas, return_exceptions=True)

    cotacoes = []
    for produto, preco_resultado in zip(candidatos, precos):
        preco = preco_resultado if isinstance(preco_resultado, float) else None
        est   = estoque_total(produto)

        # Monta string de filiais se houver estoque distribuído
        filiais = produto.get("estoques") or []
        prazo = None
        if filiais and not produto.get("estoque"):
            sigs = [f["sigla"] for f in filiais[:3]]
            prazo = "Filiais: " + ", ".join(sigs)

        status = (
            StatusCotacao.SUCESSO    if preco and est > 0
            else StatusCotacao.SEM_ESTOQUE if preco and est == 0
            else StatusCotacao.NAO_ENCONTRADO
        )

        cotacoes.append(Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            preco=preco,
            estoque=est,
            prazo_entrega=prazo,
            codigo_produto=f"{produto.get('codigo', '')} — {produto.get('descricao', '')} [{produto.get('marca', '')}]",
            status=status,
        ))

    # Ordena: com preço+estoque primeiro, menor preço
    cotacoes.sort(key=lambda c: (
        0 if c.status == StatusCotacao.SUCESSO else
        1 if c.status == StatusCotacao.SEM_ESTOQUE else 2,
        c.preco or 9999,
    ))

    return cotacoes if cotacoes else [Cotacao(
        distribuidor=DISTRIBUIDOR_NOME,
        status=StatusCotacao.NAO_ENCONTRADO,
    )]


# ══════════════════════════════════════════════════════════
#  CLASSE SCRAPER — interface com o Manager
# ══════════════════════════════════════════════════════════

class WsrptScraper:
    DISTRIBUIDOR_ID   = DISTRIBUIDOR_ID
    DISTRIBUIDOR_NOME = DISTRIBUIDOR_NOME
    is_marketplace    = True  # retorna lista, sem Playwright no manager

    def __init__(self):
        self.usuario  = os.getenv("WSRPT_USUARIO", "")
        self.senha    = os.getenv("WSRPT_SENHA",   "")
        self._sessao: SessaoWsrpt | None = None

    async def ofertas(self) -> list[Cotacao]:
        """Retorna todos os produtos em oferta."""
        if not self._sessao or not self._sessao.ok():
            self._sessao = await autenticar(self.usuario, self.senha)
        if not self._sessao.ok():
            return []
        return await buscar_ofertas(self._sessao)

    async def cotar_multiplo(
        self,
        referencia: str,
        ignorar_sellers: list[str] | None = None,
    ) -> list[Cotacao]:

        if not self.usuario or not self.senha:
            return [Cotacao(
                distribuidor=DISTRIBUIDOR_NOME,
                status=StatusCotacao.ERRO,
                erro_msg="Credenciais não configuradas (WSRPT_USUARIO / WSRPT_SENHA)",
            )]

        # Autentica se ainda não tem sessão válida
        if not self._sessao or not self._sessao.ok():
            logger.info("[WSRPT] Autenticando...")
            self._sessao = await autenticar(self.usuario, self.senha)

        if not self._sessao.ok():
            return [Cotacao(
                distribuidor=DISTRIBUIDOR_NOME,
                status=StatusCotacao.ERRO,
                erro_msg="Falha no login — verifique credenciais e screenshot em /tmp/wsrpt_login_erro.png",
            )]

        return await cotar_wsrpt(self._sessao, referencia)


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

def _extrair_params_url(url: str) -> dict:
    """Extrai query params de uma URL."""
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(url).query)
    return {k: v[0] for k, v in qs.items()}


def _cookies_str(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ══════════════════════════════════════════════════════════
#  TESTE LOCAL
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    USUARIO = os.getenv("WSRPT_USUARIO") or input("E-mail: ")
    SENHA   = os.getenv("WSRPT_SENHA")   or input("Senha: ")
    print("\nO que deseja fazer?")
    print("  1 — Buscar por referência")
    print("  2 — Listar ofertas")
    opcao = input("Opção [1/2]: ").strip() or "1"
    REF = ""
    if opcao == "1":
        REF = input("Referência para buscar: ").strip()

    async def main():
        print(f"\n[1/3] Autenticando em {URL_BASE}...")
        sessao = await autenticar(USUARIO, SENHA)

        if not sessao.ok():
            print("✗ Falha no login.")
            print("  Verifique /tmp/wsrpt_login_erro.png para debug")
            return

        print(f"✓ Sessão ok!  pedido={sessao.pedido}  firma={sessao.firma}  local={sessao.local}")

        if opcao == "2":
            print(f"\n[2/3] Buscando ofertas...")
            cotacoes = await buscar_ofertas(sessao)
        else:
            print(f"\n[2/3] Buscando produtos para '{REF}'...")
            produtos = await buscar_produtos(sessao, REF)
            print(f"  {len(produtos)} produto(s) encontrado(s)")
            if not produtos:
                print("Nenhum resultado.")
                return
            print(f"\n[3/3] Buscando preços (top {MAX_PRODUTOS_PRECO})...")
            cotacoes = await cotar_wsrpt(sessao, REF)

        print(f"\n{'#':<3} {'PRODUTO':<55} {'PREÇO':>10}  {'EST':>6}")
        print("─" * 80)
        for i, c in enumerate(cotacoes, 1):
            prod  = (c.codigo_produto or "—")[:53]
            preco = f"R$ {c.preco:.2f}" if c.preco else "       —"
            est   = str(c.estoque or 0)
            prazo = f"  [{c.prazo_entrega}]" if c.prazo_entrega else ""
            print(f"{i:<3} {prod:<55} {preco:>10}  {est:>6}{prazo}")

    asyncio.run(main())
