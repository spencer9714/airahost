import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY ?? "";

/** Browser client — uses anon key, respects RLS */
export function getSupabaseBrowser() {
  return createClient(supabaseUrl, supabaseAnonKey);
}

/** Server client — uses service role key if available, otherwise falls back to anon key */
export function getSupabaseAdmin() {
  return createClient(supabaseUrl, supabaseServiceKey || supabaseAnonKey);
}
