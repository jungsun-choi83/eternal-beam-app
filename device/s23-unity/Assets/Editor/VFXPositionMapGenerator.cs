using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public class VFXPositionMapGenerator : EditorWindow
{
    public Texture2D sourceTexture;

    public int pointCount = 512;

    public float brightnessThreshold = 0.5f;

    public float alphaThreshold = 0.01f;

    public float shapeWidth = 3.5f;

    public float shapeHeight = 2.6f;


    [MenuItem("Tools/VFX/Position Map Generator")]
    public static void ShowWindow()
    {
        GetWindow<VFXPositionMapGenerator>(
            "VFX Position Map"
        );
    }


    private void OnGUI()
    {
        GUILayout.Label(
            "VFX Position Map Generator",
            EditorStyles.boldLabel
        );

        EditorGUILayout.Space();

        sourceTexture =
            (Texture2D)EditorGUILayout.ObjectField(
                "Source Texture",
                sourceTexture,
                typeof(Texture2D),
                false
            );

        pointCount =
            EditorGUILayout.IntField(
                "Point Count",
                pointCount
            );

        brightnessThreshold =
            EditorGUILayout.Slider(
                "Brightness Threshold",
                brightnessThreshold,
                0f,
                1f
            );

        alphaThreshold =
            EditorGUILayout.Slider(
                "Alpha Threshold",
                alphaThreshold,
                0f,
                1f
            );

        shapeWidth =
            EditorGUILayout.FloatField(
                "Shape Width",
                shapeWidth
            );

        shapeHeight =
            EditorGUILayout.FloatField(
                "Shape Height",
                shapeHeight
            );

        EditorGUILayout.Space();

        GUI.enabled =
            sourceTexture != null &&
            pointCount > 0;

        if (GUILayout.Button(
            "Generate Position Map",
            GUILayout.Height(35)))
        {
            Generate();
        }

        GUI.enabled = true;
    }


    private void Generate()
    {
        string sourcePath =
            AssetDatabase.GetAssetPath(sourceTexture);

        if (string.IsNullOrEmpty(sourcePath))
        {
            Debug.LogError(
                "[VFX Position Map] Source Texture missing."
            );

            return;
        }


        // ==================================================
        // Load ORIGINAL PNG
        // ==================================================

        byte[] rawBytes =
            File.ReadAllBytes(sourcePath);

        Texture2D source =
            new Texture2D(
                2,
                2,
                TextureFormat.RGBA32,
                false,
                true
            );


        if (!ImageConversion.LoadImage(
            source,
            rawBytes,
            false))
        {
            Debug.LogError(
                "[VFX Position Map] Failed to load source image."
            );

            DestroyImmediate(source);
            return;
        }


        Color[] pixels =
            source.GetPixels();

        int width =
            source.width;

        int height =
            source.height;


        // ==================================================
        // Find valid mask pixels
        // ==================================================

        List<Vector2Int> validPixels =
            new List<Vector2Int>();

        List<float> densities =
            new List<float>();


        float minAlpha = 1f;
        float maxAlpha = 0f;


        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                Color pixel =
                    pixels[y * width + x];


                minAlpha =
                    Mathf.Min(
                        minAlpha,
                        pixel.a
                    );

                maxAlpha =
                    Mathf.Max(
                        maxAlpha,
                        pixel.a
                    );


                // Ignore transparent background.
                if (pixel.a < alphaThreshold)
                    continue;


                float brightness =
                    (
                        pixel.r +
                        pixel.g +
                        pixel.b
                    ) / 3.0f;


                if (brightness <
                    brightnessThreshold)
                {
                    continue;
                }


                validPixels.Add(
                    new Vector2Int(
                        x,
                        y
                    )
                );


                // Brightness controls density.
                // Alpha also contributes so soft edges
                // naturally contain fewer particles.

                float density =
                    brightness *
                    pixel.a;


                densities.Add(
                    density
                );
            }
        }


        Debug.Log(
            $"[VFX Position Map] SOURCE\n" +
            $"Size: {width} x {height}\n" +
            $"Alpha Range: {minAlpha:F4} - {maxAlpha:F4}\n" +
            $"Valid Pixels: {validPixels.Count}"
        );


        if (validPixels.Count == 0)
        {
            Debug.LogError(
                "[VFX Position Map] No valid mask pixels."
            );

            DestroyImmediate(source);
            return;
        }


        // ==================================================
        // Create FLOAT position texture
        // ==================================================

        Texture2D pointTexture =
            new Texture2D(
                pointCount,
                1,
                TextureFormat.RGBAFloat,
                false,
                true
            );


        Color[] pointPixels =
            new Color[pointCount];

        System.Random random =
            new System.Random();


        // ==================================================
        // Density weighted sampling
        // ==================================================

        for (int i = 0;
             i < pointCount;
             i++)
        {
            Vector2Int p;


            while (true)
            {
                int index =
                    random.Next(
                        validPixels.Count
                    );


                if (random.NextDouble()
                    <= densities[index])
                {
                    p =
                        validPixels[index];

                    break;
                }
            }


            float nx =
                (float)p.x /
                (width - 1);

            float ny =
                (float)p.y /
                (height - 1);


            float worldX =
                (nx - 0.5f) *
                shapeWidth;


            // Flip Y to match Godot version.

            float worldY =
                (0.5f - ny) *
                shapeHeight;


            pointPixels[i] =
                new Color(
                    worldX,
                    worldY,
                    0f,
                    1f
                );
        }

        float minX = float.MaxValue;
        float maxX = float.MinValue;
        float minY = float.MaxValue;
        float maxY = float.MinValue;

        for (int i = 0; i < pointPixels.Length; i++)
        {
            Color p = pointPixels[i];

            minX = Mathf.Min(minX, p.r);
            maxX = Mathf.Max(maxX, p.r);

            minY = Mathf.Min(minY, p.g);
            maxY = Mathf.Max(maxY, p.g);
        }

        Debug.Log(
            $"[POSITION DATA]\n" +
            $"X = {minX:F3} ~ {maxX:F3}\n" +
            $"Y = {minY:F3} ~ {maxY:F3}\n" +
            $"First = {pointPixels[0]}"
        );

        pointTexture.SetPixels(
            pointPixels
        );

        pointTexture.Apply(
            false,
            false
        );


        // ==================================================
        // Save as EXR
        // ==================================================

        string directory =
            Path.GetDirectoryName(
                sourcePath
            );

        string sourceName =
            Path.GetFileNameWithoutExtension(
                sourcePath
            );

        string outputPath =
            $"{directory}/" +
            $"{sourceName}_PositionMap.exr";


        byte[] exrData =
            pointTexture.EncodeToEXR(
                Texture2D.EXRFlags.OutputAsFloat
            );


        File.WriteAllBytes(
            outputPath,
            exrData
        );


        // Clean temporary textures.

        DestroyImmediate(
            pointTexture
        );

        DestroyImmediate(
            source
        );


        AssetDatabase.Refresh();


        // ==================================================
        // Configure EXR
        // ==================================================

        TextureImporter importer =
            AssetImporter.GetAtPath(
                outputPath
            ) as TextureImporter;


        if (importer != null)
        {
            importer.textureType =
                TextureImporterType.Default;

            importer.sRGBTexture =
                false;

            importer.mipmapEnabled =
                false;

            importer.textureCompression =
                TextureImporterCompression.Uncompressed;

            importer.filterMode =
                FilterMode.Point;

            importer.wrapMode =
                TextureWrapMode.Clamp;

            importer.SaveAndReimport();
        }


        Texture2D generated =
            AssetDatabase.LoadAssetAtPath<Texture2D>(
                outputPath
            );


        Selection.activeObject =
            generated;

        EditorGUIUtility.PingObject(
            generated
        );


        Debug.Log(
            $"[VFX Position Map] DONE\n" +
            $"Generated Points: {pointCount}\n" +
            $"Valid Mask Pixels: {validPixels.Count}\n" +
            $"Output: {outputPath}"
        );
    }
}