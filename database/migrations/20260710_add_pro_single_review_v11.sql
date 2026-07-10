ALTER TABLE pro_resume_run ADD COLUMN previous_failed_run_id VARCHAR(64);
ALTER TABLE pro_candidate_review ADD COLUMN contract_version VARCHAR(64);
ALTER TABLE pro_candidate_review ADD COLUMN candidate_input_hash VARCHAR(64);
ALTER TABLE pro_candidate_review ADD COLUMN ranking_tie_break_reason VARCHAR(256);
ALTER TABLE pro_candidate_review ADD COLUMN ranking_version VARCHAR(64);
