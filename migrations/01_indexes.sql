-- Production Database Indexes for Fit24
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Step Logs Optimization
-- Speeds up: Syncing steps (upsert), fetching today's steps, and history.
CREATE INDEX IF NOT EXISTS idx_step_logs_user_date 
ON public.step_logs (user_id, log_date DESC);

-- 2. Leaderboard Optimization
-- Speeds up: Weekly and Daily leaderboard aggregations.
CREATE INDEX IF NOT EXISTS idx_step_logs_date_steps 
ON public.step_logs (log_date, steps DESC);

-- 3. Activity Sessions Optimization
-- Speeds up: Fetching recent sessions and calculating total points.
CREATE INDEX IF NOT EXISTS idx_activity_sessions_user_created 
ON public.activity_sessions (user_id, created_at DESC);

-- 4. User Profiles Index
-- Speeds up: Public profile lookups.
-- Note: Assuming user_profiles table exists as mentioned in main.py comments.
CREATE INDEX IF NOT EXISTS idx_user_profiles_username 
ON public.user_profiles (username) WHERE username IS NOT NULL;
