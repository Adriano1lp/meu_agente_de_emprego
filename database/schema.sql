PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

CREATE TABLE IF NOT EXISTS user_profiles (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL DEFAULT 1,
    nome_completo TEXT,
    email TEXT,
    telefone TEXT,
    linkedin TEXT,
    resumo_profissional TEXT,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_profile_versions (
    profile_version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, version),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_profile_versions_user_id
    ON user_profile_versions (user_id);

CREATE TABLE IF NOT EXISTS user_documents (
    document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    original_content_type TEXT NOT NULL,
    original_file_path TEXT NOT NULL,
    extracted_text_path TEXT,
    bytes_received INTEGER NOT NULL,
    checksum_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    CHECK (document_type IN ('cv'))
);

CREATE INDEX IF NOT EXISTS idx_user_documents_user_id
    ON user_documents (user_id);

CREATE TABLE IF NOT EXISTS embedding_runs (
    embedding_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    document_id INTEGER,
    embedding_model TEXT NOT NULL,
    chunks INTEGER NOT NULL DEFAULT 0,
    chroma_dir TEXT NOT NULL,
    cv_file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES user_documents (document_id) ON DELETE SET NULL,
    CHECK (status IN ('pending', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_embedding_runs_user_id
    ON embedding_runs (user_id);

CREATE TABLE IF NOT EXISTS processing_runs (
    processing_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    input_text TEXT NOT NULL,
    job_data_json TEXT,
    matching_json TEXT,
    optimization_json TEXT,
    response_text TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    CHECK (status IN ('pending', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_processing_runs_user_id
    ON processing_runs (user_id);

CREATE TABLE IF NOT EXISTS job_analysis_insights (
    insight_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    processing_run_id INTEGER,
    job_title TEXT,
    company_name TEXT,
    job_summary TEXT,
    match_score INTEGER NOT NULL DEFAULT 0,
    strengths_json TEXT NOT NULL DEFAULT '[]',
    critical_gaps_json TEXT NOT NULL DEFAULT '[]',
    matching_skills_json TEXT NOT NULL DEFAULT '[]',
    missing_skills_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'completed',
    generation_blocked INTEGER NOT NULL DEFAULT 0,
    blocked_reason TEXT,
    source TEXT NOT NULL DEFAULT 'processar',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, processing_run_id),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    FOREIGN KEY (processing_run_id) REFERENCES processing_runs (processing_run_id) ON DELETE SET NULL,
    CHECK (status IN ('pending', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_job_analysis_insights_user_created
    ON job_analysis_insights (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS development_plans (
    pdi_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_insight_ids_json TEXT NOT NULL DEFAULT '[]',
    source_processing_run_ids_json TEXT NOT NULL DEFAULT '[]',
    generated_from_limit INTEGER NOT NULL DEFAULT 10,
    title TEXT NOT NULL,
    main_objective TEXT NOT NULL,
    summary TEXT NOT NULL,
    secondary_objectives_json TEXT NOT NULL DEFAULT '[]',
    priority_areas_json TEXT NOT NULL DEFAULT '[]',
    priority_gaps_json TEXT NOT NULL DEFAULT '[]',
    strengths_to_leverage_json TEXT NOT NULL DEFAULT '[]',
    plan_70_json TEXT NOT NULL DEFAULT '[]',
    plan_20_json TEXT NOT NULL DEFAULT '[]',
    plan_10_json TEXT NOT NULL DEFAULT '[]',
    checklist_items_json TEXT NOT NULL DEFAULT '[]',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    CHECK (progress_percent >= 0 AND progress_percent <= 100),
    CHECK (status IN ('active', 'completed', 'replaced', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_development_plans_user_created
    ON development_plans (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_development_plans_user_status
    ON development_plans (user_id, status);

CREATE TABLE IF NOT EXISTS generated_files (
    generated_file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    processing_run_id INTEGER,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    public_url TEXT,
    media_type TEXT NOT NULL DEFAULT 'application/pdf',
    bytes_size INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    FOREIGN KEY (processing_run_id) REFERENCES processing_runs (processing_run_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_generated_files_user_id
    ON generated_files (user_id);

INSERT OR IGNORE INTO schema_migrations (version, name)
VALUES (1, 'initial_sqlite_schema');
