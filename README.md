# Ajustes B3 → Supabase

Coleta diária dos preços de ajuste de **todos os contratos futuros** da B3
(boi, milho, soja, café, DI, dólar, Ibovespa...) e grava no Supabase.
A API REST do próprio Supabase serve os dados.

## 1. Supabase
1. No projeto: **SQL Editor → New query**, cole `sql/schema.sql` e rode.
2. Em **Project Settings → API Keys**, copie:
   - a **URL** do projeto (`https://xxxx.supabase.co`)
   - a **Secret key** (`sb_secret_...`), ou a `service_role` legada. Use-a só no coletor, nunca no front-end.

## 2. GitHub
1. Crie um repositório (pode ser privado) e suba esta pasta.
2. **Settings → Secrets and variables → Actions → New repository secret**:
   - `SUPABASE_URL`
   - `SUPABASE_SECRET_KEY`
3. Em **Actions → Coleta de ajustes B3 → Run workflow** rode manualmente para testar.
   Para carga histórica, preencha `inicio` (ex.: `2026-01-02`).

O agendamento roda de seg a sex às 19h e 20h30 (Brasília) e reprocessa os últimos 5 dias.
O upsert evita duplicatas e repõe dias que tenham falhado.

## 3. Consultando (API)
Use a **publishable/anon key** (somente leitura):

```bash
KEY=sb_publishable_...
URL=https://xxxx.supabase.co/rest/v1

# Contrato mais próximo de cada mercadoria, último pregão
curl "$URL/ajustes_contrato_atual?select=ticker,ajuste,data_pregao" -H "apikey: $KEY"

# Todos os vencimentos de boi no último pregão
curl "$URL/ajustes_ultimo_pregao?mercadoria=eq.BGI&order=mes_vencimento" -H "apikey: $KEY"

# Histórico de um contrato
curl "$URL/ajustes?ticker=eq.BGIZ26&order=data_pregao.desc&limit=30" -H "apikey: $KEY"

# Curva de DI numa data
curl "$URL/ajustes?mercadoria=eq.DI1&data_pregao=eq.2026-11-12&select=ticker,taxa_ajuste&order=mes_vencimento" -H "apikey: $KEY"
```

## Rodando localmente
```bash
pip install -r requirements.txt
python coletor.py --data 2026-11-12 --dry-run      # só mostra, não grava
export SUPABASE_URL=... SUPABASE_SECRET_KEY=...
python coletor.py --data 2026-11-12                # grava
python -m pytest tests                              # testes
```

## Observações
- **Fonte:** B3 → Pesquisa por pregão, boletim simplificado de derivativos (SPRD).
  Se ele não estiver disponível, o coletor usa o Price Report completo (PR / BVBG.086).
- **Filtro:** grava só os futuros no padrão `RAIZ + mês + ano` (ex.: `BGIZ26`) que tenham ajuste.
  Opções, ações e spreads ficam de fora.
- **Vencimentos novos** entram automaticamente, e os vencidos param de aparecer.
- `mes_vencimento` é o 1º dia do mês do vencimento. O dia exato varia por contrato.
- **DI1/DAP/DDI:** `ajuste` é o PU e `taxa_ajuste` é a taxa (% a.a.).
- **Licença:** redistribuir publicamente/comercialmente dados da B3 pode exigir licença de market data.

## Carga histórica
**Actions → Carga histórica → Run workflow**, informe `inicio` (e opcionalmente `fim`).
Com `somente_ativos` marcado, grava só o histórico dos contratos que estão em negociação hoje.
A B3 disponibiliza os boletins a partir de 2018 aproximadamente.
