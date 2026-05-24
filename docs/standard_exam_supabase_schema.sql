create schema if not exists standard_exam;

create table if not exists standard_exam.exam_sets (
    id uuid primary key,
    title text not null,
    subject text not null default 'math',
    exam_type text not null default 'thpt_graduation_standard',
    source_file text not null,
    source_hash text not null,
    source_id text,
    exam_index integer not null,
    start_page integer,
    end_page integer,
    total_pages integer,
    expected_question_count integer not null default 22,
    expected_item_count integer not null default 34,
    extracted_question_count integer not null default 0,
    max_score numeric not null default 10,
    status text not null,
    audit_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists standard_exam.exam_sections (
    id uuid primary key,
    exam_set_id uuid not null references standard_exam.exam_sets(id) on delete cascade,
    section_code text not null,
    title text,
    question_type text not null,
    section_order integer not null,
    expected_count integer not null,
    extracted_count integer not null default 0,
    max_score numeric not null,
    scoring_rule jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (exam_set_id, section_code)
);

create table if not exists standard_exam.questions (
    id uuid primary key,
    source_code text not null unique,
    question_type text not null,
    question_text text not null,
    option_a text,
    option_b text,
    option_c text,
    option_d text,
    correct_answer text,
    statements jsonb,
    numeric_answer text,
    explanation text,
    topic text,
    subtopic text,
    chapter text,
    difficulty text,
    canonical_topic_id integer,
    canonical_topic_code text,
    canonical_topic_title text,
    canonical_subtopic_id integer,
    canonical_subtopic_code text,
    canonical_subtopic_title text,
    needs_visual boolean not null default false,
    visual_type text,
    visual_bbox jsonb,
    visual_table jsonb,
    image_url text,
    raw_text jsonb not null default '{}'::jsonb,
    answer_source text not null default 'missing',
    needs_review boolean not null default false,
    is_published boolean not null default false,
    created_at timestamptz not null default now()
);

create table if not exists standard_exam.exam_questions (
    id uuid primary key,
    exam_set_id uuid not null references standard_exam.exam_sets(id) on delete cascade,
    section_id uuid not null references standard_exam.exam_sections(id) on delete cascade,
    question_id uuid not null references standard_exam.questions(id),
    section_code text not null,
    question_number integer not null,
    display_order integer not null,
    page_number integer,
    source_hint text,
    max_score numeric not null,
    scoring_rule_snapshot jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (exam_set_id, section_id, question_number)
);

create table if not exists standard_exam.ingest_runs (
    id uuid primary key,
    exam_set_id uuid references standard_exam.exam_sets(id) on delete set null,
    source_file text not null,
    source_hash text not null,
    mode text not null,
    status text not null,
    stats_json jsonb not null default '{}'::jsonb,
    failures_json jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'uq_standard_exam_sets_source_exam'
    ) then
        alter table standard_exam.exam_sets
            add constraint uq_standard_exam_sets_source_exam
            unique (source_hash, exam_index);
    end if;
end $$;

create index if not exists idx_standard_exam_sets_source_hash
    on standard_exam.exam_sets(source_hash);

create index if not exists idx_standard_exam_questions_source_code
    on standard_exam.questions(source_code);

create index if not exists idx_standard_exam_exam_questions_exam_order
    on standard_exam.exam_questions(exam_set_id, display_order);

grant usage on schema standard_exam to anon, authenticated, service_role;
grant all on all tables in schema standard_exam to service_role;
grant select on all tables in schema standard_exam to authenticated;
grant select on all tables in schema standard_exam to anon;
alter default privileges in schema standard_exam grant all on tables to service_role;
alter default privileges in schema standard_exam grant select on tables to authenticated;
alter default privileges in schema standard_exam grant select on tables to anon;
notify pgrst, 'reload schema';
