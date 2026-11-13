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
