-- Run once in the Supabase SQL editor (Database -> SQL editor -> New query -> Run).
-- One row per signed-in user. Row level security is on with no policies, so the browser can never read or
-- write this table; only the backend, which holds the secret key, can.
create table if not exists public.pm_users (
  user_id uuid primary key references auth.users (id) on delete cascade,
  email text,
  snaptrade_user_id text,
  snaptrade_secret_enc text,          -- encrypted by the backend with TOKEN_KEY before it is stored
  holdings jsonb,                     -- last normalised positions and accounts
  analysis jsonb,                     -- last full analysis shown in the portal
  analysis_at timestamptz,
  source text,                        -- 'brokerage' or 'typed'
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.pm_users enable row level security;
