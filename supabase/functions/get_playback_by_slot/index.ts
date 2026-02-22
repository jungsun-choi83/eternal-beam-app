// Supabase Edge Function: 기기가 슬롯 번호로 재생 정보 조회
// 호출 예: GET https://프로젝트.supabase.co/functions/v1/get_playback_by_slot?slot=2
// 헤더: Authorization: Bearer (anon key)

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  try {
    const url = new URL(req.url)
    const slotParam = url.searchParams.get('slot')
    const slotNumber = slotParam ? parseInt(slotParam, 10) : NaN

    if (!Number.isInteger(slotNumber) || slotNumber < 1) {
      return new Response(
        JSON.stringify({ error: 'slot 번호를 1 이상 정수로 보내주세요. 예: ?slot=2' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    const supabaseUrl = Deno.env.get('SUPABASE_URL')!
    const supabaseKey = Deno.env.get('SUPABASE_ANON_KEY')!
    const supabase = createClient(supabaseUrl, supabaseKey)

    const { data, error } = await supabase.rpc('get_playback_by_slot', {
      p_slot_number: slotNumber,
    })

    if (error) {
      return new Response(
        JSON.stringify({ error: error.message }),
        { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    const row = Array.isArray(data) ? data[0] : data
    if (!row) {
      return new Response(
        JSON.stringify({ error: '해당 슬롯에 매핑된 콘텐츠가 없습니다.', slot_number: slotNumber }),
        { status: 404, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }

    return new Response(JSON.stringify({ slot_number: slotNumber, ...row }), {
      status: 200,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    })
  } catch (e) {
    return new Response(
      JSON.stringify({ error: String(e) }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )
  }
})
