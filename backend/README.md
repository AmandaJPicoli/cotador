# 🔧 Cotador B2B — Autopeças

Sistema de cotação paralela em portais B2B de distribuidores de autopeças.
Consulta 4–10 portais simultaneamente com Playwright e retorna resultados ordenados por preço.

---

## 📁 Estrutura

```
cotador-autopecas/
├── backend/
│   ├── main.py                        ← FastAPI (endpoints)
│   ├── models.py                      ← Pydantic models
│   ├── requirements.txt
│   ├── railway.toml                   ← Config deploy Railway
│   ├── .env.example                   ← Modelo de credenciais
│   └── scrapers/
│       ├── base_scraper.py            ← Classe base (herde desta)
│       ├── manager.py                 ← Orquestrador paralelo
│       └── distribuidor_template.py  ← Template comentado
└── frontend/
    └── index.html                     ← Interface de cotação
```

---

## 🚀 Setup Local

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium

cp .env.example .env
# edite .env com suas credenciais

python main.py                  # sobe em localhost:8000
```

### 2. Frontend

Abra `frontend/index.html` no navegador.
Configure `API_URL` no topo do script: `const API_URL = "http://localhost:8000";`

---

## 🏗️ Adicionando um Distribuidor

### Passo 1 — Criar o scraper

Copie `scrapers/distribuidor_template.py`:

```bash
cp scrapers/distribuidor_template.py scrapers/distribuidora_nova.py
```

### Passo 2 — Preencher os seletores

Abra o portal no Chrome, pressione **F12**, clique em cada elemento
(campo de login, botão, célula de preço etc.) e copie o seletor CSS:
> Botão direito no elemento → **Copy** → **Copy selector**

Edite a classe e substitua:
```python
DISTRIBUIDOR_ID   = "nova"                          # identificador único
DISTRIBUIDOR_NOME = "Distribuidora Nova Ltda"
URL_LOGIN         = "https://portal.nova.com.br/login"
SEL_INPUT_USUARIO = "#field-login"                  # seletor real
SEL_INPUT_SENHA   = "#field-senha"
SEL_BTN_LOGIN     = ".btn-entrar"
SEL_PRECO         = "td.coluna-preco"
# ...
```

### Passo 3 — Credenciais

Adicione em `.env`:
```
NOVA_USUARIO=seu_login
NOVA_SENHA=sua_senha
```

### Passo 4 — Registrar

Em `scrapers/manager.py`:
```python
from scrapers.distribuidora_nova import DistribuidoraNova

SCRAPERS_REGISTRADOS = [
    DistribuidoraNova,
    # outros scrapers...
]
```

---

## ☁️ Deploy na Railway

1. Push do projeto para um repositório GitHub
2. Na Railway: **New Project → Deploy from GitHub**
3. Selecione a pasta `backend/` como root directory
4. Adicione as variáveis de ambiente (credenciais) em **Variables**
5. A Railway detecta o `railway.toml` automaticamente

> O `railway.toml` já inclui `playwright install chromium` no startCommand.

---

## 🌐 Endpoints da API

| Método | Endpoint          | Descrição                          |
|--------|-------------------|------------------------------------|
| GET    | `/health`         | Healthcheck                        |
| GET    | `/distribuidores` | Lista distribuidores registrados   |
| POST   | `/cotar`          | Dispara cotação                    |

### POST /cotar

**Request:**
```json
{
  "referencia": "90915-YZZD2",
  "distribuidores": null
}
```
`distribuidores: null` → consulta todos. Passe `["id_a", "id_b"]` para filtrar.

**Response:**
```json
{
  "referencia": "90915-YZZD2",
  "cotacoes": [
    {
      "distribuidor": "AutoDist Nacional",
      "preco": 87.50,
      "estoque": 24,
      "prazo_entrega": "1 dia útil",
      "status": "sucesso",
      "melhor_preco": true
    }
  ],
  "total_consultados": 6,
  "total_com_estoque": 4,
  "tempo_ms": 1840
}
```

---

## ⚠️ Pontos de Atenção

| Problema               | Solução                                                           |
|------------------------|-------------------------------------------------------------------|
| Captcha no portal      | Serviço 2captcha (pago) ou resolução manual na primeira sessão   |
| Layout mudou           | Atualizar seletores CSS no scraper do distribuidor               |
| Sessão expirou         | Implementar relogin automático (detecção por redirect p/ login)  |
| Rate limiting          | Adicionar `await asyncio.sleep(1)` entre buscas rápidas          |
| SPA demora renderizar  | Usar `wait_for_selector` em vez de `wait_for_load_state`          |

---

## 📝 Notas sobre portais SPA (React/Angular)

Portais dinâmicos podem exigir espera explícita após a busca:

```python
# Em vez de:
await self._aguardar_navegacao()

# Use:
await self._page.wait_for_selector(".produto-resultado", timeout=15000)
# ou aguarda que elemento de loading desapareça:
await self._page.wait_for_selector(".spinner", state="hidden", timeout=15000)
```
