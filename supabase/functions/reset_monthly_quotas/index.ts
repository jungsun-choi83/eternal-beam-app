/**
 * 매월 1일 실행: 사용자별 generation_count_this_month 리셋
 * - basic, premium: 리셋
 * - lifetime: 리셋 (30회 유지)
 *
 * 배포: supabase functions deploy reset_monthly_quotas
 * 스케줄: Supabase Cron 또는 외부 Cron (매월 1일 00:00)
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  const supabaseUrl = Deno.env.get('SUPABASE_URL') ?? ''
  const supabaseServiceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
  if (!supabaseUrl || !supabaseServiceKey) {
    return new Response(
      JSON.stringify({ error: 'Supabase env not set' }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )
  }

  const supabase = createClient(supabaseUrl, supabaseServiceKey)

  const nextMonth = new Date()
  nextMonth.setMonth(nextMonth.getMonth() + 1)
  const quotaResetAt = nextMonth.toISOString()

  const { error } = await supabase
    .from('user_quotas')
    .update({
      generation_count_this_month: 0,
      quota_reset_at: quotaResetAt,
      updated_at: new Date().toISOString(),
    })
    .neq('user_id', '')

  if (error) {
    console.error('reset 실패:', error)
    return new Response(
      JSON.stringify({ error: error.message }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )
  }

  return new Response(
    JSON.stringify({ success: true, message: 'Monthly quota reset completed' }),
    { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
  )
})
