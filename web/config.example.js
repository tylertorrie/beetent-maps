/* Copy this file to `config.js` and fill in your Supabase project's values.
 *
 *   Supabase dashboard → Project Settings → API
 *     • Project URL        → url
 *     • Project API keys → anon / public   → anonKey
 *
 * The ANON key is safe to ship to the browser — row-level security (RLS) policies
 * on the `fields` table are what actually gate access. NEVER put the service_role
 * key here (that one lives only on the office desktop, in supabase_config.json).
 *
 * config.js is gitignored. With no config.js present the app runs in DEMO MODE
 * against the bundled sample field, so you can see the shell without a backend.
 */
window.SUPABASE_CONFIG = {
  url: "https://YOUR-PROJECT.supabase.co",
  anonKey: "YOUR-ANON-PUBLIC-KEY",
};
