-- =========================================================
-- Disparo da coleta pelo próprio Supabase (pg_cron + pg_net)
-- O agendamento do GitHub Actions é pouco confiável (atrasa horas ou pula
-- execuções). Aqui o Supabase chama a API do GitHub nos horários certos.
-- 1) Troque COLE_SEU_TOKEN_AQUI pelo token do GitHub (fine-grained, só o repo
--    api_fut, permissão "Actions: Read and write").
-- 2) Rode tudo no SQL Editor. Horários em UTC (Brasília = UTC-3).
-- =========================================================
create extension if not exists pg_cron;
create extension if not exists pg_net;

-- guarda o token criptografado no Vault
select vault.create_secret('COLE_SEU_TOKEN_AQUI', 'github_token', 'Token para disparar a coleta B3');

create or replace function public.disparar_coleta_b3()
returns bigint
language sql
security definer
set search_path = public, extensions
as $$
  select net.http_post(
    url := 'https://api.github.com/repos/saulojardimise/api_fut/actions/workflows/coleta.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'github_token'),
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'supabase-pg-cron'
    ),
    body := '{"ref":"main","inputs":{"verificar":"true"}}'::jsonb
  );
$$;
revoke all on function public.disparar_coleta_b3() from public, anon, authenticated;

-- 20h15 e 21h30 de Brasília, seg a sex
select cron.schedule('coleta-b3-20h15', '15 23 * * 1-5', 'select public.disparar_coleta_b3()');
select cron.schedule('coleta-b3-21h30', '30 0 * * 2-6',  'select public.disparar_coleta_b3()');

-- Teste imediato (deve aparecer uma execução nova na aba Actions em ~1 min):
select public.disparar_coleta_b3();
-- Resposta do GitHub (status 204 = ok):  select status_code, content from net._http_response order by created desc limit 1;
