-- Fit24 Master Database Setup
-- Run this in your Supabase SQL Editor to create all required tables and indexes.
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. User Profiles Table
CREATE TABLE IF NOT EXISTS public.user_profiles (
  id             uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  phone          text,
  gender         text,
  age            int,
  weight_kg      numeric(5,1),
  height_cm      int,
  daily_goal     int  DEFAULT 8000,
  points         int  DEFAULT 0, -- Total Fit Points (Coins)
  focus_areas    text[]  DEFAULT '{}',
  exercise_freq  text,
  exercise_types text[]  DEFAULT '{}',
  name           text,
  city           text,
  avatar_url     text,
  tracking_dark_map       boolean DEFAULT false,
  tracking_audio_feedback boolean DEFAULT true,
  tracking_countdown_timer boolean DEFAULT false,
  tracking_keep_screen_on  boolean DEFAULT false,
  tracking_auto_pause      boolean DEFAULT false,
  tracking_auto_resume     boolean DEFAULT true,
  created_at     timestamptz DEFAULT now(),
  updated_at     timestamptz DEFAULT now()
);

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'users manage own profile') THEN
        CREATE POLICY "users manage own profile" ON public.user_profiles FOR ALL USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_user_profiles_name ON public.user_profiles (name);


-- 2. Step Logs Table
CREATE TABLE IF NOT EXISTS public.step_logs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  log_date    date NOT NULL,
  steps       integer NOT NULL DEFAULT 0,
  calories    integer GENERATED ALWAYS AS (steps / 20) STORED,
  distance_m  integer GENERATED ALWAYS AS (steps * 75 / 100) STORED,
  synced_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, log_date)
);

ALTER TABLE public.step_logs ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'users manage own logs') THEN
        CREATE POLICY "users manage own logs" ON public.step_logs FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_step_logs_user_date 
ON public.step_logs (user_id, log_date DESC);

CREATE INDEX IF NOT EXISTS idx_step_logs_date_steps 
ON public.step_logs (log_date, steps DESC);


-- 3. Activity Sessions Table
CREATE TABLE IF NOT EXISTS public.activity_sessions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  type        int NOT NULL, -- 0: walk, 1: run, 2: cycle
  distance    numeric(10,2) NOT NULL DEFAULT 0,
  duration    integer NOT NULL DEFAULT 0,
  steps       integer NOT NULL DEFAULT 0,
  calories    integer NOT NULL DEFAULT 0,
  fit_points  integer NOT NULL DEFAULT 0,
  route       jsonb DEFAULT '[]',
  created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.activity_sessions ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'users manage own sessions') THEN
        CREATE POLICY "users manage own sessions" ON public.activity_sessions FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_activity_sessions_user_created 
ON public.activity_sessions (user_id, created_at DESC);


-- 4. User Follows (Social)
CREATE TABLE IF NOT EXISTS public.user_follows (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  follower_id  uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  following_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at   timestamptz DEFAULT now(),
  UNIQUE(follower_id, following_id)
);

ALTER TABLE public.user_follows ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'users manage own follows') THEN
        CREATE POLICY "users manage own follows" ON public.user_follows FOR ALL USING (auth.uid() = follower_id) WITH CHECK (auth.uid() = follower_id);
    END IF;
END $$;


-- 5. Challenges & Claims
CREATE TABLE IF NOT EXISTS public.challenges (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title             text NOT NULL,
  description       text,
  reward_coins      int NOT NULL,
  requirement_type  text NOT NULL, -- 'steps', 'distance', 'calories'
  requirement_value int NOT NULL,
  is_daily          boolean DEFAULT true,
  created_at        timestamptz DEFAULT now()
);

ALTER TABLE public.challenges ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'everyone can view challenges') THEN
        CREATE POLICY "everyone can view challenges" ON public.challenges FOR SELECT USING (true);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.user_claims (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  challenge_id uuid NOT NULL REFERENCES public.challenges(id) ON DELETE CASCADE,
  date         date NOT NULL,
  reward       int NOT NULL,
  created_at   timestamptz DEFAULT now(),
  UNIQUE(user_id, challenge_id, date)
);

ALTER TABLE public.user_claims ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'users manage own claims') THEN
        CREATE POLICY "users manage own claims" ON public.user_claims FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;
