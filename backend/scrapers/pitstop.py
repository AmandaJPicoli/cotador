"""
PITSTOP — Scraper via API VTEX (SEM Playwright, SEM login)
===========================================================
Suporta três modos de busca:

  1. Código exato      → "90915-YZZD2"
  2. Texto simples     → "filtro oleo"
  3. Texto composto    → "bucha jumello"  (retorna só resultados que contêm
                          TODAS as palavras no nome/descrição do produto)

A API VTEX recebe o termo via ?ft= (fulltext). Para buscas compostas,
o filtro "contém todas as palavras" é aplicado localmente após o retorno.
"""

import asyncio
import httpx
import logging
import unicodedata
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import Cotacao, StatusCotacao

logger = logging.getLogger(__name__)

DISTRIBUIDOR_ID   = "pitstop"
DISTRIBUIDOR_NOME = "PitStop"

MAX_SELLERS = 5   # quantos sellers retornar por busca
VTEX_RESULTS = 20 # quantos produtos pedir à VTEX (mais produtos → mais sellers)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://www.pitstop.com.br/",
}

BASE_URL = "https://www.pitstop.com.br"


def _normalizar(texto: str) -> str:
    """Remove acentos e converte para minúsculas para comparação."""
    return unicodedata.normalize("NFD", texto.lower()).encode("ascii", "ignore").decode()


def _contem_todos(texto_produto: str, palavras: list[str]) -> bool:
    """
    Retorna True se o texto do produto contém TODAS as palavras da busca.
    Ignora acentos e maiúsculas/minúsculas.
    Ex: texto="Bucha Jumello Suspensão", palavras=["bucha","jumello"] → True
    """
    normalizado = _normalizar(texto_produto)
    return all(_normalizar(p) in normalizado for p in palavras)


async def cotar_pitstop(
    referencia: str,
    ignorar_sellers: list[str] | None = None,
) -> list[Cotacao]:
    """
    Consulta o marketplace PitStop via API VTEX pública.

    Aceita:
      - Código de peça:    "90915-YZZD2"
      - Texto simples:     "filtro oleo"
      - Texto composto:    "bucha jumello"  ← filtra produtos que contenham
                           TODAS as palavras

    Retorna lista de Cotacao ordenada por preço (com estoque primeiro).
    """
    # Palavras para o filtro local "contém todos"
    palavras_busca = referencia.strip().split()

    # Monta URL — VTEX recebe o termo inteiro via ?ft=
    # httpx cuida do URL-encoding dos espaços automaticamente
    url = (
        f"{BASE_URL}/api/catalog_system/pub/products/search"
        f"?ft={referencia}&_from=0&_to={VTEX_RESULTS - 1}"
    )

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            produtos = resp.json()

    except httpx.HTTPStatusError as e:
        logger.error(f"[PitStop] HTTP {e.response.status_code}: {e}")
        return [Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            status=StatusCotacao.ERRO,
            erro_msg=f"HTTP {e.response.status_code}",
        )]
    except Exception as e:
        logger.error(f"[PitStop] Erro: {e}")
        return [Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            status=StatusCotacao.ERRO,
            erro_msg=str(e),
        )]

    if not produtos:
        return [Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            status=StatusCotacao.NAO_ENCONTRADO,
        )]

    ignorar = set(ignorar_sellers or [])
    ofertas: list[dict] = []

    for produto in produtos:
        nome_produto = produto.get("productName", "")
        descricao    = produto.get("description", "")

        # ── Filtro "contém todos" ────────────────────────────────────────────
        # Para busca com múltiplas palavras, garante que o produto realmente
        # contém TODAS as palavras (ex: "bucha jumello" não retorna só "bucha").
        if len(palavras_busca) > 1:
            texto_completo = f"{nome_produto} {descricao}"
            if not _contem_todos(texto_completo, palavras_busca):
                logger.debug(f"[PitStop] Filtrado (não contém todas): {nome_produto}")
                continue

        skus = produto.get("items", [])

        for sku in skus:
            for seller in sku.get("sellers", []):
                seller_id   = seller.get("sellerId", "")
                seller_nome = seller.get("sellerName", "Loja não identificada")

                if seller_id in ignorar:
                    logger.debug(f"[PitStop] Seller ignorado: {seller_id}")
                    continue

                oferta  = seller.get("commertialOffer", {})
                preco   = oferta.get("Price")
                estoque = oferta.get("AvailableQuantity", 0)

                if preco and preco > 0:
                    ofertas.append({
                        "seller_id":   seller_id,
                        "seller_nome": seller_nome,
                        "preco":       float(preco),
                        "estoque":     int(estoque),
                        "descricao":   nome_produto,
                    })

    if not ofertas:
        return [Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            status=StatusCotacao.NAO_ENCONTRADO,
        )]

    # Ordena: com estoque + menor preço primeiro
    ofertas.sort(key=lambda o: (0 if o["estoque"] > 0 else 1, o["preco"]))

    # Deduplica por seller_id
    vistos: set[str] = set()
    ofertas_unicas = []
    for o in ofertas:
        if o["seller_id"] not in vistos:
            vistos.add(o["seller_id"])
            ofertas_unicas.append(o)

    cotacoes = []
    for o in ofertas_unicas[:MAX_SELLERS]:
        status = StatusCotacao.SUCESSO if o["estoque"] > 0 else StatusCotacao.SEM_ESTOQUE
        cotacoes.append(Cotacao(
            distribuidor=DISTRIBUIDOR_NOME,
            vendedor=o["seller_nome"],
            seller_id=o["seller_id"],
            preco=o["preco"],
            estoque=o["estoque"],
            prazo_entrega=None,
            codigo_produto=o["descricao"],
            status=status,
            melhor_preco=False,
        ))

    return cotacoes


class PitStopScraper:
    DISTRIBUIDOR_ID   = DISTRIBUIDOR_ID
    DISTRIBUIDOR_NOME = DISTRIBUIDOR_NOME
    is_marketplace    = True

    async def cotar_multiplo(
        self,
        referencia: str,
        ignorar_sellers: list[str] | None = None,
    ) -> list[Cotacao]:
        return await cotar_pitstop(referencia, ignorar_sellers)


# ── Teste local ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def main():
        print("Exemplos de busca:")
        print("  90915-YZZD2        → código exato")
        print("  filtro oleo        → texto simples")
        print("  bucha jumello      → texto composto (contém todas as palavras)")
        print()
        ref = input("Busca: ").strip()
        print(f"\nConsultando PitStop para: '{ref}'\n")

        resultados = await cotar_pitstop(ref)

        if not resultados or resultados[0].status == StatusCotacao.NAO_ENCONTRADO:
            print("Nenhum resultado encontrado.")
            return

        print(f"{'PRODUTO':<45} {'VENDEDOR':<35} {'SELLER_ID':<15} {'PREÇO':>10}  {'ESTOQUE':>8}")
        print("─" * 115)
        for c in resultados:
            prod   = (c.codigo_produto or "")[:43]
            vend   = (c.vendedor or "—")[:33]
            sid    = (c.seller_id or "—")[:13]
            preco  = f"R$ {c.preco:>8.2f}" if c.preco else "         —"
            est    = str(c.estoque or 0)
            print(f"{prod:<45} {vend:<35} {sid:<15} {preco}  {est:>8}")

    asyncio.run(main())
