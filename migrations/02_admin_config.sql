-- Admin Configuration and System Settings for Fit24
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. System Config Table
CREATE TABLE IF NOT EXISTS public.system_config (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL,
  updated_at  timestamptz DEFAULT now()
);

ALTER TABLE public.system_config ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'everyone can read config') THEN
        CREATE POLICY "everyone can read config" ON public.system_config FOR SELECT USING (true);
    END IF;
END $$;

-- 2. Initial Spin Wheel Configuration
INSERT INTO public.system_config (key, value)
VALUES ('spin_wheel', '{
  "prizes": [
    {"label": "100 Coins", "value": 100, "chance": 40},
    {"label": "500 Coins", "value": 500, "chance": 20},
    {"label": "1,000 Coins", "value": 1000, "chance": 10},
    {"label": "2,000 Coins", "value": 2000, "chance": 5},
    {"label": "5,000 Coins", "value": 5000, "chance": 2},
    {"label": "Better Luck", "value": 0, "chance": 23}
  ],
  "cooldown_hours": 24
}')
ON CONFLICT (key) DO NOTHING;

-- 3. Notification Logs (for Broadcast History)
CREATE TABLE IF NOT EXISTS public.notifications (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  message     text NOT NULL,
  target      text DEFAULT 'all',
  created_at  timestamptz DEFAULT now()
);

ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'everyone can view notifications') THEN
        CREATE POLICY "everyone can view notifications" ON public.notifications FOR SELECT USING (true);
    END IF;
END $$;

-- 4. Admin Activity Logs (Audit Trail)
CREATE TABLE IF NOT EXISTS public.admin_logs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  admin_id    uuid REFERENCES auth.users(id),
  action      text NOT NULL,
  target      text, -- e.g., 'User: Aditya', 'Config: SpinWheel'
  details     jsonb,
  created_at  timestamptz DEFAULT now()
);

ALTER TABLE public.admin_logs ENABLE ROW LEVEL SECURITY;
-- Note: In production, only admins should see this. For now, we'll allow select.
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'everyone can view logs') THEN
        CREATE POLICY "everyone can view logs" ON public.admin_logs FOR SELECT USING (true);
    END IF;
END $$;
