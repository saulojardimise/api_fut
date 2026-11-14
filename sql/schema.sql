-- =========================================================
-- Ajustes diários de contratos futuros da B3
-- Rode este arquivo inteiro no SQL Editor do Supabase.
-- =========================================================

create table if not exists public.ajustes (
  data_pregao           date        not null,
  ticker                text        not null,   -- ex.: BGIZ26
  mercadoria            text        not null,   -- ex.: BGI
  mes_vencimento        date        not null,   -- 1º dia do mês de vencimento (ex.: 2026-12-01)
  ajuste                numeric,                -- preço de ajuste do dia (PU no caso do DI)
  ajuste_anterior       numeric,                -- ajuste do pregão anterior
  taxa_ajuste           numeric,                -- taxa de ajuste (DI1, DAP etc.); nulo nos demais
  taxa_ajuste_anterior  numeric,
  variacao_pts          numeric,                -- ajuste - ajuste_anterior (pontos)
  contratos_abertos     bigint,
  volume_financeiro     numeric,
  abertura              numeric,
  minima                numeric,
  maxima                numeric,
  fechamento            numeric,                -- último negócio
  preco_medio           numeric,
  contratos_negociados  bigint,
  negocios              bigint,
  atualizado_em         timestamptz not null default now(),
  primary key (data_pregao, ticker)
);

create index if not exists ajustes_mercadoria_data_idx
  on public.ajustes (mercadoria, data_pregao desc);

create index if not exists ajustes_ticker_data_idx
  on public.ajustes (ticker, data_pregao desc);

-- Nomes amigáveis das mercadorias (opcional, para a API)
create table if not exists public.mercadorias (
  codigo     text primary key,
  nome       text not null,
  unidade    text
);

insert into public.mercadorias (codigo, nome, unidade) values
  ('BGI', 'Boi gordo',                     'R$/@'),
  ('CCM', 'Milho',                         'R$/saca 60kg'),
  ('SJC', 'Soja (Chicago, liquidação financeira)', 'US$/saca 60kg'),
  ('SOY', 'Soja (FOB Santos)',             'US$/t'),
  ('ICF', 'Café arábica 4/5',              'US$/saca 60kg'),
  ('ETN', 'Etanol hidratado',              'R$/m³'),
  ('DI1', 'Taxa DI de um dia',             'PU / % a.a.'),
  ('DAP', 'Cupom de IPCA',                 'PU / % a.a.'),
  ('DDI', 'Cupom cambial',                 'PU / % a.a.'),
  ('DOL', 'Dólar comercial',               'R$/US$ 1.000'),
  ('WDO', 'Mini dólar',                    'R$/US$ 1.000'),
  ('IND', 'Ibovespa',                      'pontos'),
  ('WIN', 'Mini Ibovespa',                 'pontos'),
  ('BIT', 'Bitcoin',                       'R$')
on conflict (codigo) do nothing;

-- ---------------------------------------------------------
-- View: último ajuste de cada contrato (todos os vencimentos)
-- ---------------------------------------------------------
create or replace view public.ajustes_ultimo_pregao
with (security_invoker = true) as
select a.*
from public.ajustes a
where a.data_pregao = (select max(data_pregao) from public.ajustes);

-- ---------------------------------------------------------
-- View: "contrato atual" = vencimento mais próximo de cada
-- mercadoria no último pregão
-- ---------------------------------------------------------
create or replace view public.ajustes_contrato_atual
with (security_invoker = true) as
select distinct on (a.mercadoria) a.*
from public.ajustes_ultimo_pregao a
order by a.mercadoria, a.mes_vencimento asc;

-- ---------------------------------------------------------
-- Segurança: leitura pública (chave anon/publishable),
-- escrita só com a chave secreta (service_role) do coletor.
-- ---------------------------------------------------------
alter table public.ajustes     enable row level security;
alter table public.mercadorias enable row level security;

drop policy if exists "leitura publica" on public.ajustes;
create policy "leitura publica" on public.ajustes
  for select to anon, authenticated using (true);

drop policy if exists "leitura publica" on public.mercadorias;
create policy "leitura publica" on public.mercadorias
  for select to anon, authenticated using (true);
-- =========================================================
-- Monitoramento da coleta (rode no SQL Editor do Supabase)
-- =========================================================

-- Registro de cada execução do coletor
create table if not exists public.coletas (
  id             bigint generated always as identity primary key,
  executado_em   timestamptz not null default now(),
  status         text        not null check (status in ('ok', 'alerta', 'erro')),
  linhas         integer     not null default 0,
  ultimo_pregao  date,
  mensagem       text,
  execucao       text          -- id da execução no GitHub Actions
);
create index if not exists coletas_executado_em_idx on public.coletas (executado_em desc);

alter table public.coletas enable row level security;
drop policy if exists "leitura publica" on public.coletas;
create policy "leitura publica" on public.coletas
  for select to anon, authenticated using (true);

-- Painel de saúde: uma linha resumindo a situação atual
create or replace view public.saude
with (security_invoker = true) as
select
  (select max(data_pregao) from public.ajustes)                               as ultimo_pregao,
  (select count(*) from public.ajustes
     where data_pregao = (select max(data_pregao) from public.ajustes))       as contratos_ultimo_pregao,
  (select max(executado_em) from public.coletas)                              as ultima_execucao,
  (select status from public.coletas order by executado_em desc limit 1)      as status_ultima_execucao,
  (select mensagem from public.coletas order by executado_em desc limit 1)    as mensagem_ultima_execucao,
  (select count(*) from public.coletas
     where status <> 'ok' and executado_em > now() - interval '30 days')      as falhas_30_dias;
-- =========================================================
-- Séries contínuas (rode no SQL Editor do Supabase)
-- =========================================================

-- serie_continua: em cada pregão, o vencimento "da vez" de cada mercadoria.
-- Regra: o vencimento mais curto com volume relevante (>= 50% do mais negociado),
-- sem nunca voltar para um vencimento anterior (evita idas e vindas na rolagem).
drop view if exists public.serie_continua_ajustada;
drop view if exists public.serie_continua;

create view public.serie_continua
with (security_invoker = true) as
with volumes as (
  select mercadoria, data_pregao, mes_vencimento, contratos_negociados,
         max(contratos_negociados) over (partition by mercadoria, data_pregao) as maior_volume
  from public.ajustes
  where contratos_negociados > 0
),
mais_negociado as (
  -- o vencimento mais curto que tenha pelo menos metade do volume do mais negociado
  -- (evita que um negócio isolado num vencimento longo "puxe" a série)
  select distinct on (mercadoria, data_pregao) mercadoria, data_pregao, mes_vencimento
  from volumes
  where contratos_negociados >= 0.5 * maior_volume
  order by mercadoria, data_pregao, mes_vencimento
),
vez as (
  select mercadoria, data_pregao,
         max(mes_vencimento) over (partition by mercadoria order by data_pregao) as mes_vencimento
  from mais_negociado
)
select a.mercadoria, a.data_pregao, a.ticker, a.mes_vencimento,
       a.abertura, a.minima, a.maxima, a.fechamento, a.ajuste, a.preco_medio,
       a.contratos_negociados, a.volume_financeiro, a.negocios, a.contratos_abertos
from vez v
join public.ajustes a
  on a.mercadoria = v.mercadoria and a.data_pregao = v.data_pregao and a.mes_vencimento = v.mes_vencimento;

-- serie_continua_ajustada: mesma série, ajustada proporcionalmente nas rolagens.
-- Na troca de vencimento, todo o histórico anterior é multiplicado por
-- (ajuste do novo vencimento / ajuste do antigo) no pregão anterior à troca.
-- Os preços mais recentes ficam iguais aos reais; os antigos são "puxados" para
-- eliminar os degraus. Variações percentuais diárias são preservadas.
create view public.serie_continua_ajustada
with (security_invoker = true) as
with s as (
  select c.*,
         lag(c.ticker)      over w as ticker_anterior,
         lag(c.data_pregao) over w as pregao_anterior
  from public.serie_continua c
  window w as (partition by c.mercadoria order by c.data_pregao)
),
rolagens as (
  select s.*,
         case when s.ticker_anterior is not null and s.ticker_anterior <> s.ticker
              then novo.ajuste / nullif(velho.ajuste, 0) end as fator_rolagem
  from s
  left join public.ajustes novo  on novo.ticker  = s.ticker          and novo.data_pregao  = s.pregao_anterior
  left join public.ajustes velho on velho.ticker = s.ticker_anterior and velho.data_pregao = s.pregao_anterior
),
fatores as (
  select r.*,
         coalesce(exp(sum(ln(r.fator_rolagem)) over (
           partition by r.mercadoria order by r.data_pregao desc
           rows between unbounded preceding and 1 preceding)), 1) as fator
  from rolagens r
)
select mercadoria, data_pregao, ticker,
       round(abertura   * fator, 2) as abertura,
       round(minima     * fator, 2) as minima,
       round(maxima     * fator, 2) as maxima,
       round(fechamento * fator, 2) as fechamento,
       round(ajuste     * fator, 2) as ajuste,
       contratos_negociados, volume_financeiro, negocios,
       (ticker_anterior is not null and ticker_anterior <> ticker) as rolagem,
       round(fator, 6) as fator_ajuste
from fatores;

notify pgrst, 'reload schema';
