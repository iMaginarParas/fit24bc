-- OTP Codes Table for custom OTP delivery (e.g., via Resend)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.otp_codes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email       text NOT NULL,
  code        text NOT NULL,
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz DEFAULT now()
);

-- Index for cleanup and lookup
CREATE INDEX IF NOT EXISTS idx_otp_codes_email ON public.otp_codes(email);

-- Optional: Enable RLS (though this is mostly managed by service role)
ALTER TABLE public.otp_codes ENABLE ROW LEVEL SECURITY;

-- No public access
DROP POLICY IF EXISTS "No public access" ON public.otp_codes;
CREATE POLICY "No public access" ON public.otp_codes FOR ALL USING (false);
