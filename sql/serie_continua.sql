-- Série contínua: em cada pregão, o vencimento mais negociado de cada mercadoria
create or replace view public.serie_continua
with (security_invoker = true) as
select distinct on (a.mercadoria, a.data_pregao)
  a.mercadoria, a.data_pregao, a.ticker,
  a.abertura, a.minima, a.maxima, a.fechamento, a.ajuste,
  a.preco_medio, a.contratos_negociados, a.volume_financeiro, a.negocios, a.contratos_abertos
from public.ajustes a
where a.contratos_negociados > 0
order by a.mercadoria, a.data_pregao, a.contratos_negociados desc, a.mes_vencimento;

notify pgrst, 'reload schema';
