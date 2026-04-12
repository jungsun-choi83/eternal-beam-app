Shader "EternalBeam/PetShader"
{
    Properties
    {
        _MainTex ("RGB (or RGBA)", 2D) = "white" {}
        _AlphaTex ("Alpha (UseAlphaTex=1)", 2D) = "white" {}
        _UseAlphaTex ("Use Alpha Tex", Range(0,1)) = 0
        _Sharpen ("Sharpen", Range(0, 0.2)) = 0.09
        _Color ("Tint", Color) = (1,1,1,1)
    }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" }
        LOD 100

        Blend One OneMinusSrcAlpha
        ZWrite Off
        Cull Off

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_fog
            #include "UnityCG.cginc"

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
            };

            struct v2f
            {
                float2 uv : TEXCOORD0;
                UNITY_FOG_COORDS(1)
                float4 vertex : SV_POSITION;
            };

            sampler2D _MainTex;
            sampler2D _AlphaTex;
            float4 _MainTex_ST;
            float _UseAlphaTex;
            float _Sharpen;
            fixed4 _Color;

            v2f vert (appdata v)
            {
                v2f o;
                o.vertex = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                UNITY_TRANSFER_FOG(o,o.vertex);
                return o;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                fixed4 col = tex2D(_MainTex, i.uv);

                float alpha;
                if (_UseAlphaTex > 0.5)
                    alpha = tex2D(_AlphaTex, i.uv).r;
                else
                    alpha = col.a;

                col.rgb *= _Color.rgb;
                col.a = alpha * _Color.a;

                // Sharpen (하프미러 뭉개짐 보정)
                if (_Sharpen > 0.001)
                {
                    float2 dx = float2(_MainTex_ST.x / 256.0, 0);
                    float2 dy = float2(0, _MainTex_ST.y / 256.0);
                    fixed4 c0 = tex2D(_MainTex, i.uv - dx);
                    fixed4 c1 = tex2D(_MainTex, i.uv + dx);
                    fixed4 c2 = tex2D(_MainTex, i.uv - dy);
                    fixed4 c3 = tex2D(_MainTex, i.uv + dy);
                    float a0 = _UseAlphaTex > 0.5 ? tex2D(_AlphaTex, i.uv - dx).r : c0.a;
                    float a1 = _UseAlphaTex > 0.5 ? tex2D(_AlphaTex, i.uv + dx).r : c1.a;
                    float a2 = _UseAlphaTex > 0.5 ? tex2D(_AlphaTex, i.uv - dy).r : c2.a;
                    float a3 = _UseAlphaTex > 0.5 ? tex2D(_AlphaTex, i.uv + dy).r : c3.a;
                    col.rgb = col.rgb + _Sharpen * (4.0 * col.rgb - c0.rgb - c1.rgb - c2.rgb - c3.rgb);
                    col.a = alpha;
                }

                // Premultiplied Alpha 출력
                col.rgb *= col.a;
                return col;
            }
            ENDCG
        }
    }
    FallBack "Transparent/Diffuse"
}
