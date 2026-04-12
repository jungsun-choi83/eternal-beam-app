Shader "Custom/PetHologram"
{
    Properties
    {
        _MainTex      ("RGB Video",  2D) = "black" {}
        _AlphaTex     ("Alpha Mask", 2D) = "white" {}
        _UseAlphaTex  ("Use Alpha Tex", Float) = 1
        _PremulRGB    ("MainTex Is Pre-multiplied", Float) = 0
        _Brightness   ("Brightness", Range(0.5, 3)) = 1.3
        _Cutoff       ("Alpha Cutoff", Range(0, 1)) = 0.48
        _AlphaPower   ("Alpha Sharpen (fringe)", Range(0.5, 4)) = 1.35
        _RimColor     ("Rim Color", Color) = (0.65, 0.82, 1, 1)
        _RimIntensity ("Rim Intensity", Range(0, 2)) = 0.4
        _RimBand      ("Rim Edge Band", Range(0.001, 0.25)) = 0.08
        _AmbientTint  ("Ambient Tint", Color) = (1.02, 0.95, 1.02, 0)
        _TintStrength ("Tint Strength", Range(0, 0.4)) = 0.12
        _EdgeSoftness ("Edge Softness", Range(0, 0.2)) = 0.04
    }
    SubShader
    {
        Tags
        {
            "RenderPipeline" = "UniversalPipeline"
            "RenderType"     = "TransparentCutout"
            "Queue"          = "AlphaTest"
        }
        LOD 100

        Pass
        {
            Name "PetHologramClip"
            Tags { "LightMode" = "UniversalForward" }

            ZWrite On
            ZTest LEqual
            Cull Off
            Blend One Zero

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                float2 uv         : TEXCOORD0;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionHCS : SV_POSITION;
                float2 uv          : TEXCOORD0;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            TEXTURE2D(_MainTex);
            SAMPLER(sampler_MainTex);
            TEXTURE2D(_AlphaTex);
            SAMPLER(sampler_AlphaTex);

            CBUFFER_START(UnityPerMaterial)
                float4 _MainTex_ST;
                float4 _AlphaTex_ST;
                half   _UseAlphaTex;
                half   _PremulRGB;
                half   _Brightness;
                half   _Cutoff;
                half   _AlphaPower;
                half4  _RimColor;
                half   _RimIntensity;
                half   _RimBand;
                half4  _AmbientTint;
                half   _TintStrength;
                half   _EdgeSoftness;
            CBUFFER_END

            Varyings vert(Attributes IN)
            {
                Varyings OUT;
                UNITY_SETUP_INSTANCE_ID(IN);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(OUT);
                OUT.positionHCS = TransformObjectToHClip(IN.positionOS.xyz);
                OUT.uv = TRANSFORM_TEX(IN.uv, _MainTex);
                return OUT;
            }

            half4 frag(Varyings IN) : SV_Target
            {
                half4 col = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, IN.uv);

                half a;
                if (_UseAlphaTex > 0.5h)
                {
                    float2 auv = TRANSFORM_TEX(IN.uv, _AlphaTex);
                    a = SAMPLE_TEXTURE2D(_AlphaTex, sampler_AlphaTex, auv).r;
                }
                else
                {
                    a = dot(col.rgb, half3(0.299h, 0.587h, 0.114h));
                    a = smoothstep(0.05h, 0.2h, a);
                }

                // Premultiplied RGB 스트림이면 straight로 복원 (잘못된 블렌드로 유령처럼 보이는 경우 방지)
                if (_PremulRGB > 0.5h && a > 0.001h)
                    col.rgb = col.rgb / max(a, 0.001h);

                a = pow(saturate(a), max(_AlphaPower, 0.01h));

                half band = max(_RimBand, 0.001h);
                half soft = _EdgeSoftness;
                half lo = _Cutoff - soft - band * 0.5h;
                half hi = _Cutoff + soft + band * 0.5h;
                half rimMask = smoothstep(lo, _Cutoff + band * 0.25h, a)
                             * (1.0h - smoothstep(_Cutoff + band * 0.25h, hi, a));
                rimMask = saturate(rimMask);

                col.rgb *= _Brightness;
                col.rgb = lerp(col.rgb, col.rgb * _AmbientTint.rgb, _TintStrength);
                col.rgb += _RimColor.rgb * (rimMask * _RimIntensity);

                clip(a - _Cutoff);

                return half4(col.rgb, 1.0h);
            }
            ENDHLSL
        }
    }
    FallBack "Hidden/Universal Render Pipeline/FallbackError"
}
