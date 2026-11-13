-- Abertura, mínima, máxima, fechamento e volume (rode no SQL Editor do Supabase)
alter table public.ajustes
  add column if not exists abertura             numeric,
  add column if not exists minima               numeric,
  add column if not exists maxima               numeric,
  add column if not exists fechamento           numeric,  -- último negócio
  add column if not exists preco_medio          numeric,
  add column if not exists contratos_negociados bigint,
  add column if not exists negocios             bigint;

-- recria as views para incluir as novas colunas
create or replace view public.ajustes_ultimo_pregao
with (security_invoker = true) as
select a.* from public.ajustes a
where a.data_pregao = (select max(data_pregao) from public.ajustes);

drop view if exists public.ajustes_contrato_atual;
create view public.ajustes_contrato_atual
with (security_invoker = true) as
select distinct on (a.mercadoria) a.*
from public.ajustes_ultimo_pregao a
order by a.mercadoria, a.mes_vencimento asc;

notify pgrst, 'reload schema';
