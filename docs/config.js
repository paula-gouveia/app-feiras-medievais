// config.js — Credenciais do Supabase para a web app
//
// Como preencher:
//   1. Vai a https://app.supabase.com → o teu projeto
//   2. Clica em "Settings" (barra lateral) → "API"
//   3. Copia "Project URL" para supabaseUrl
//   4. Em "Project API keys", copia a chave "anon / public" para supabaseAnonKey
//
// ATENÇÃO: A chave anon é pública e segura para o browser (RLS garante só leitura).
//          NUNCA uses a service_role key aqui — essa fica apenas no servidor/Actions.

const CONFIG = {
  supabaseUrl: 'https://pimocpharqfijaowbyam.supabase.co',
  supabaseAnonKey: 'sb_publishable_qWDGNNTV_4fDQrDDc6H94g_WMiMjXXL', // anon / public key
};
