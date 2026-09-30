Add-Type -AssemblyName System.Drawing

function Generate-RadarIcon($size, $outputPath) {
    $bmp = New-Object System.Drawing.Bitmap($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)

    $rect = New-Object System.Drawing.Rectangle(0, 0, $size, $size)
    $center = $size / 2.0
    $radius = ($size / 2.0) - 1.5

    # 1. Background radial gradient
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddEllipse(1, 1, $size - 2, $size - 2)
    $pbr = New-Object System.Drawing.Drawing2D.PathGradientBrush($path)
    $pbr.CenterPoint = New-Object System.Drawing.PointF($center, $center)
    $pbr.CenterColor = [System.Drawing.Color]::FromArgb(255, 3, 105, 161) # Deep cyan/sky
    $pbr.SurroundColors = @([System.Drawing.Color]::FromArgb(255, 11, 19, 38)) # Dark navy
    $g.FillEllipse($pbr, 1, 1, $size - 2, $size - 2)

    # 2. Outer glow ring
    $outerPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(240, 14, 165, 233), [Math]::Max(1.0, $size / 24.0))
    $g.DrawEllipse($outerPen, 1, 1, $size - 2, $size - 2)

    # 3. Inner concentric rings
    $ringPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(100, 56, 189, 248), [Math]::Max(1.0, $size / 36.0))
    $r1 = $radius * 0.68
    $g.DrawEllipse($ringPen, $center - $r1, $center - $r1, $r1 * 2, $r1 * 2)
    $r2 = $radius * 0.38
    $g.DrawEllipse($ringPen, $center - $r2, $center - $r2, $r2 * 2, $r2 * 2)

    # 4. Crosshairs
    $crossPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(120, 56, 189, 248), [Math]::Max(1.0, $size / 40.0))
    $g.DrawLine($crossPen, 2, $center, $size - 2, $center)
    $g.DrawLine($crossPen, $center, 2, $center, $size - 2)

    # 5. Sweep sector
    $sweepBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (New-Object System.Drawing.PointF($center, $center)),
        (New-Object System.Drawing.PointF($size, $center * 0.3)),
        [System.Drawing.Color]::FromArgb(180, 56, 189, 248),
        [System.Drawing.Color]::FromArgb(10, 16, 185, 129)
    )
    $g.FillPie($sweepBrush, 1, 1, $size - 2, $size - 2, -45, 65)

    # 6. Target Blips
    $blip1Brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 16, 185, 129)) # Emerald
    $b1x = $center + ($radius * 0.48)
    $b1y = $center - ($radius * 0.38)
    $b1s = [Math]::Max(2.0, $size / 16.0)
    $g.FillEllipse($blip1Brush, $b1x - ($b1s/2), $b1y - ($b1s/2), $b1s, $b1s)

    # 7. Center Bullseye
    $coreBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 248, 250, 252))
    $coreS = [Math]::Max(2.0, $size / 14.0)
    $g.FillEllipse($coreBrush, $center - ($coreS/2), $center - ($coreS/2), $coreS, $coreS)

    $bmp.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose()
    $bmp.Dispose()
    Write-Output "Generated $outputPath ($size x $size)"
}

Generate-RadarIcon 16 "D:\Projects\gcc-job-radar\chrome-extension\icons\icon16.png"
Generate-RadarIcon 48 "D:\Projects\gcc-job-radar\chrome-extension\icons\icon48.png"
Generate-RadarIcon 128 "D:\Projects\gcc-job-radar\chrome-extension\icons\icon128.png"
