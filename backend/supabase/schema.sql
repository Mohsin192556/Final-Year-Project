insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'qanoon-legal-documents',
  'qanoon-legal-documents',
  false,
  31457280,
  array['application/pdf']
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create table if not exists public.legal_documents (
  id uuid primary key,
  storage_path text not null unique,
  title text not null,
  citation text not null,
  jurisdiction text not null,
  source_url text not null,
  original_filename text not null,
  verified boolean not null default false,
  excluded_pages integer[] not null default '{}',
  quality jsonb,
  fingerprint text,
  status text not null default 'pending'
    check (status in ('pending', 'ready')),
  uploaded_at timestamptz not null default now()
);

alter table public.legal_documents enable row level security;

create table if not exists public.legal_chunks (
  id bigint generated always as identity primary key,
  index_version uuid not null,
  content text not null,
  metadata jsonb not null,
  created_at timestamptz not null default now(),
  search_vector tsvector generated always as
    (to_tsvector('english'::regconfig, content)) stored
);

create index if not exists legal_chunks_version_idx
  on public.legal_chunks (index_version);
create index if not exists legal_chunks_search_idx
  on public.legal_chunks using gin (search_vector);

alter table public.legal_chunks enable row level security;

create table if not exists public.legal_index_state (
  id boolean primary key default true check (id),
  active_version uuid,
  fingerprints jsonb not null default '{}'::jsonb,
  document_count integer not null default 0,
  indexed_page_count integer not null default 0,
  chunk_count integer not null default 0,
  indexed_at timestamptz
);

alter table public.legal_index_state enable row level security;

grant all on table public.legal_documents to service_role;
grant all on table public.legal_chunks to service_role;
grant all on table public.legal_index_state to service_role;
grant usage, select on all sequences in schema public to service_role;

create or replace function public.search_legal_chunks(
  p_terms text,
  p_target_article text,
  p_limit integer default 200
)
returns table (content text, metadata jsonb)
language sql
stable
security definer
set search_path = public, extensions
as $$
  with active as (
    select active_version
    from public.legal_index_state
    where id = true and active_version is not null
  ),
  query as (
    select case
      when nullif(trim(p_terms), '') is null then null
      else to_tsquery('english'::regconfig, p_terms)
    end as terms
  )
  select chunk.content, chunk.metadata
  from public.legal_chunks as chunk
  cross join active
  cross join query
  where chunk.index_version = active.active_version
    and (
      (query.terms is not null and chunk.search_vector @@ query.terms)
      or (
        p_target_article ~ '^[0-9]+[a-z]?$'
        and chunk.content ~ (
          '(^|\n)[[:space:]]*' || p_target_article || '[.]'
        )
      )
    )
  order by
    case
      when p_target_article ~ '^[0-9]+[a-z]?$'
        and chunk.content ~ (
          '(^|\n)[[:space:]]*' || p_target_article || '[.]'
        )
      then 1 else 0
    end desc,
    coalesce(ts_rank_cd(chunk.search_vector, query.terms), 0) desc
  limit greatest(1, least(coalesce(p_limit, 200), 500));
$$;

create or replace function public.activate_legal_index(
  p_active_version uuid,
  p_fingerprints jsonb,
  p_document_count integer,
  p_indexed_page_count integer,
  p_chunk_count integer,
  p_indexed_at timestamptz
)
returns void
language plpgsql
security definer
set search_path = public, extensions
as $$
begin
  insert into public.legal_index_state (
    id,
    active_version,
    fingerprints,
    document_count,
    indexed_page_count,
    chunk_count,
    indexed_at
  )
  values (
    true,
    p_active_version,
    p_fingerprints,
    p_document_count,
    p_indexed_page_count,
    p_chunk_count,
    p_indexed_at
  )
  on conflict (id) do update set
    active_version = excluded.active_version,
    fingerprints = excluded.fingerprints,
    document_count = excluded.document_count,
    indexed_page_count = excluded.indexed_page_count,
    chunk_count = excluded.chunk_count,
    indexed_at = excluded.indexed_at;

  delete from public.legal_chunks
  where index_version <> p_active_version
    and created_at < now() - interval '1 day';
end;
$$;

revoke all on function public.search_legal_chunks(text, text, integer) from public;
revoke all on function public.activate_legal_index(uuid, jsonb, integer, integer, integer, timestamptz) from public;
grant execute on function public.search_legal_chunks(text, text, integer) to service_role;
grant execute on function public.activate_legal_index(uuid, jsonb, integer, integer, integer, timestamptz) to service_role;
