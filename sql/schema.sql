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
