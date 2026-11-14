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
